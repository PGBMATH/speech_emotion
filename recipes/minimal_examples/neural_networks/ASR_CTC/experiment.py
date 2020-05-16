#!/usr/bin/python
import os
import speechbrain as sb
from speechbrain.decoders.ctc import ctc_greedy_decode
from speechbrain.decoders.decoders import undo_padding
from speechbrain.utils.edit_distance import wer_details_for_batch
from speechbrain.utils.train_logger import summarize_average
from speechbrain.utils.train_logger import summarize_error_rate

experiment_dir = os.path.dirname(os.path.abspath(__file__))
params_file = os.path.join(experiment_dir, "params.yaml")
data_folder = "../../../../../samples/audio_samples/nn_training_samples"
data_folder = os.path.abspath(experiment_dir + data_folder)
with open(params_file) as fin:
    params = sb.yaml.load_extended_yaml(fin, {"data_folder": data_folder})


class CTCBrain(sb.core.Brain):
    def forward(self, x, init_params=False):
        id, wavs, lens = x
        feats = params.compute_features(wavs, init_params)
        feats = params.mean_var_norm(feats, lens)

        x = params.rnn(feats, init_params)
        x = params.lin(x, init_params)
        outputs = params.softmax(x)

        return outputs, lens

    def compute_objectives(self, predictions, targets, train=True):
        predictions, lens = predictions
        ids, phns, phn_lens = targets
        loss = params.compute_cost(predictions, phns, [lens, phn_lens])

        if not train:
            seq = ctc_greedy_decode(predictions, lens, blank_id=-1)
            phns = undo_padding(phns, phn_lens)
            stats = {"PER": wer_details_for_batch(ids, phns, seq)}
            return loss, stats

        return loss

    def on_epoch_end(self, epoch, train_stats, valid_stats):
        print("Epoch %d complete" % epoch)
        print("Train loss: %.2f" % summarize_average(train_stats["loss"]))
        print("Valid loss: %.2f" % summarize_average(valid_stats["loss"]))
        print("Valid PER: %.2f" % summarize_error_rate(valid_stats["PER"]))


train_set = params.train_loader()
ctc_brain = CTCBrain(
    modules=[params.rnn, params.lin],
    optimizer=params.optimizer,
    first_input=next(iter(train_set[0])),
)
ctc_brain.fit(range(params.N_epochs), train_set, params.valid_loader())
test_stats = ctc_brain.evaluate(params.test_loader())
print("Test PER: %.2f" % summarize_error_rate(test_stats["PER"]))

# For such a small dataset, the PER can be unpredictable.
# Instead, check that at the end of training, the error is acceptable.
assert summarize_average(test_stats["loss"]) < 2.0
