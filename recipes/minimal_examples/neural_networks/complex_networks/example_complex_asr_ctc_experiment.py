#!/usr/bin/python
import os
import speechbrain as sb
from speechbrain.decoders.ctc import ctc_greedy_decode
from speechbrain.decoders.decoders import undo_padding
from speechbrain.utils.edit_distance import wer_details_for_batch
from speechbrain.utils.train_logger import summarize_average
from speechbrain.utils.train_logger import summarize_error_rate

experiment_dir = os.path.dirname(os.path.realpath(__file__))
hyperparams_file = os.path.join(experiment_dir, "hyperparams.yaml")
data_folder = "../../../../samples/audio_samples/nn_training_samples"
data_folder = os.path.realpath(os.path.join(experiment_dir, data_folder))
with open(hyperparams_file) as fin:
    hyperparams = sb.yaml.load_extended_yaml(fin, {"data_folder": data_folder})


class CTCBrain(sb.core.Brain):
    def compute_forward(self, x, stage="train", init_params=False):
        id, wavs, lens = x
        feats = hyperparams.compute_features(wavs, init_params)
        feats = hyperparams.mean_var_norm(feats, lens)

        x = hyperparams.conv1(feats, init_params=init_params)
        x = hyperparams.cln1(x, init_params=init_params)
        x = hyperparams.activation()(x)
        x = hyperparams.pooling(x)

        x = hyperparams.conv2(feats, init_params=init_params)
        x = hyperparams.cln2(x, init_params=init_params)
        x = hyperparams.activation()(x)
        x = hyperparams.pooling(x)

        x = hyperparams.rnn(x, init_params=init_params)

        x = hyperparams.lin(x, init_params)
        outputs = hyperparams.softmax(x)

        return outputs, lens

    def compute_objectives(self, predictions, targets, stage="train"):
        predictions, lens = predictions
        ids, phns, phn_lens = targets
        loss = hyperparams.compute_cost(predictions, phns, lens, phn_lens)

        stats = {}
        if stage != "train":
            seq = ctc_greedy_decode(predictions, lens, blank_id=-1)
            phns = undo_padding(phns, phn_lens)
            stats["PER"] = wer_details_for_batch(ids, phns, seq)

        return loss, stats

    def on_epoch_end(self, epoch, train_stats, valid_stats):
        print("Epoch %d complete" % epoch)
        print("Train loss: %.2f" % summarize_average(train_stats["loss"]))
        print("Valid loss: %.2f" % summarize_average(valid_stats["loss"]))
        print("Valid PER: %.2f" % summarize_error_rate(valid_stats["PER"]))


train_set = hyperparams.train_loader()
first_x, first_y = next(iter(train_set))
ctc_brain = CTCBrain(
    modules=[
        hyperparams.conv1,
        hyperparams.conv2,
        hyperparams.pooling,
        hyperparams.rnn,
        hyperparams.lin,
    ],
    optimizer=hyperparams.optimizer,
    first_inputs=[first_x],
)
ctc_brain.fit(
    range(hyperparams.N_epochs), train_set, hyperparams.valid_loader()
)
test_stats = ctc_brain.evaluate(hyperparams.test_loader())
print("Test PER: %.2f" % summarize_error_rate(test_stats["PER"]))


# Integration test: check that the model overfits the training data
def test_error():
    assert ctc_brain.avg_train_loss < 0.4
