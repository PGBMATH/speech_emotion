#!/usr/bin/python
import os
import speechbrain as sb
from speechbrain.data_io.data_io import put_bos_token
from speechbrain.decoders.seq2seq import RNNGreedySearcher
from speechbrain.utils.train_logger import summarize_average
from speechbrain.utils.train_logger import summarize_error_rate
from speechbrain.decoders.decoders import undo_padding
from speechbrain.utils.edit_distance import wer_details_for_batch

experiment_dir = os.path.dirname(os.path.abspath(__file__))
params_file = os.path.join(experiment_dir, "params.yaml")
data_folder = "../../../../../samples/audio_samples/nn_training_samples"
data_folder = os.path.abspath(experiment_dir + data_folder)
with open(params_file) as fin:
    params = sb.yaml.load_extended_yaml(fin, {"data_folder": data_folder})

searcher = RNNGreedySearcher(
    modules=[params.emb, params.decoder, params.lin, params.softmax],
    bos_index=params.bos,
    eos_index=params.eos,
    min_decode_ratio=0,
    max_decode_ratio=0.1,
)


class seq2seqBrain(sb.core.Brain):
    def compute_forward(self, x, y, train_mode=True, init_params=False):
        id, wavs, wav_lens = x
        id, phns, phn_lens = y
        feats = params.compute_features(wavs, init_params)
        feats = params.mean_var_norm(feats, wav_lens)
        x = params.rnn(feats, init_params=init_params)

        y_in = put_bos_token(phns, bos_index=params.bos)
        e_in = params.emb(y_in)
        h, w = params.decoder(e_in, x, wav_lens, init_params=init_params)
        logits = params.lin(h, init_params=init_params)
        outputs = params.softmax(logits)

        if not train_mode:
            seq, _ = searcher(x, wav_lens)
            return outputs, seq

        return outputs

    def compute_objectives(self, predictions, targets, train_mode=True):
        if train_mode:
            outputs = predictions
        else:
            outputs, seq = predictions

        ids, phns, phn_lens = targets
        loss = params.compute_cost(outputs, phns, [phn_lens, phn_lens])

        if not train_mode:
            phns = undo_padding(phns, phn_lens)
            stats = {"PER": wer_details_for_batch(ids, phns, seq)}
            return loss, stats

        return loss

    def fit_batch(self, batch):
        inputs, targets = batch
        predictions = self.compute_forward(inputs, targets)
        loss = self.compute_objectives(predictions, targets)
        loss.backward()
        self.optimizer(self.modules)
        return {"loss": loss.detach()}

    def evaluate_batch(self, batch):
        inputs, targets = batch
        out = self.compute_forward(inputs, targets, train_mode=False)
        loss, stats = self.compute_objectives(out, targets, train_mode=False)
        stats["loss"] = loss.detach()
        return stats

    def on_epoch_end(self, epoch, train_stats, valid_stats):
        print("Epoch %d complete" % epoch)
        print("Train loss: %.2f" % summarize_average(train_stats["loss"]))
        print("Valid loss: %.2f" % summarize_average(valid_stats["loss"]))
        print("Valid PER: %.2f" % summarize_error_rate(valid_stats["PER"]))


train_set = params.train_loader()
first_x, first_y = next(zip(*train_set))
seq2seq_brain = seq2seqBrain(
    modules=[params.rnn, params.emb, params.decoder, params.lin],
    optimizer=params.optimizer,
    first_inputs=[first_x, first_y],
)
seq2seq_brain.fit(range(params.N_epochs), train_set, params.valid_loader())
test_stats = seq2seq_brain.evaluate(params.test_loader())
print("Test loss: %.2f" % summarize_average(test_stats["loss"]))


def test_error():
    assert summarize_average(test_stats["loss"]) < 15.0
