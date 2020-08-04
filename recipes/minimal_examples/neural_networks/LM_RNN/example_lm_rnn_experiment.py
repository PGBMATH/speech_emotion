#!/usr/bin/env python3
import os
import torch
import math
import speechbrain as sb

from speechbrain.data_io.data_io import prepend_bos_token
from speechbrain.data_io.data_io import append_eos_token
from speechbrain.utils.train_logger import summarize_average

experiment_dir = os.path.dirname(os.path.realpath(__file__))
hyperparams_file = os.path.join(experiment_dir, "hyperparams.yaml")
data_folder = "../../../../samples/audio_samples/nn_training_samples"
data_folder = os.path.realpath(os.path.join(experiment_dir, data_folder))
with open(hyperparams_file) as fin:
    hyperparams = sb.yaml.load_extended_yaml(fin, {"data_folder": data_folder})


# Define training procedure
class LMBrain(sb.core.Brain):
    def compute_forward(self, y, stage="train", init_params=False):
        ids, phns, phn_lens = y
        y_in = prepend_bos_token(phns, bos_index=hyperparams.bos_index)
        e_in = hyperparams.emb(y_in, init_params=init_params)
        h_rnn = hyperparams.rnn(e_in, init_params=init_params)
        logits = hyperparams.lin(h_rnn, init_params)
        pout = hyperparams.log_softmax(logits)
        return pout

    def compute_objectives(self, predictions, targets, stage="train"):
        pout = predictions
        ids, phns, phn_lens = targets

        abs_length = torch.round(phn_lens * phns.shape[1])

        # Append eos token at the end of the label sequences
        phns_with_eos = append_eos_token(
            phns, length=abs_length, eos_index=hyperparams.eos_index
        )

        # convert to speechbrain-style relative length
        rel_length = (abs_length + 1) / phns.shape[1]
        loss = hyperparams.compute_cost(pout, phns_with_eos, length=rel_length)

        return loss, {}

    def fit_batch(self, batch):
        inputs = batch[0]
        predictions = self.compute_forward(inputs)
        loss, stats = self.compute_objectives(predictions, inputs)
        loss.backward()
        self.optimizer.step()
        self.optimizer.zero_grad()
        stats["loss"] = loss.detach()
        return stats

    def evaluate_batch(self, batch, stage="test"):
        inputs = batch[0]
        out = self.compute_forward(inputs, stage=stage)
        loss, stats = self.compute_objectives(out, inputs, stage=stage)
        stats["loss"] = loss.detach()
        return stats

    def on_epoch_end(self, epoch, train_stats, valid_stats=None):
        print("Epoch %d complete" % epoch)
        print("Train loss: %.2f" % summarize_average(train_stats["loss"]))
        val_loss = summarize_average(valid_stats["loss"])
        print("Valid loss: %.2f" % val_loss)
        perplexity = math.e ** val_loss
        print("Valid perplexity: %.2f" % perplexity)


train_set = hyperparams.train_loader()
valid_set = hyperparams.valid_loader()
first_y = next(iter(train_set))

lm_brain = LMBrain(
    modules=[hyperparams.rnn, hyperparams.emb, hyperparams.lin],
    optimizer=hyperparams.optimizer,
    first_inputs=first_y,
)

lm_brain.fit(hyperparams.epoch_counter, train_set, valid_set)

test_stats = lm_brain.evaluate(hyperparams.test_loader())
print("Test loss: %.2f" % summarize_average(test_stats["loss"]))


# Integration test: check that the model overfits the training data
def test_error():
    assert lm_brain.avg_train_loss < 0.15
