#!/usr/bin/python
import os
import speechbrain as sb
from speechbrain.nnet.containers import Sequential
from speechbrain.utils.train_logger import summarize_average

# Load hyperparams file
experiment_dir = os.path.dirname(os.path.abspath(__file__))
hyperparams_file = os.path.join(experiment_dir, "hyperparams.yaml")
data_folder = "../../../../../samples/voxceleb_samples/wav/"
data_folder = os.path.abspath(experiment_dir + data_folder)

with open(hyperparams_file) as fin:
    hyperparams = sb.yaml.load_extended_yaml(fin, {"data_folder": data_folder})


# Trains xvector model
class XvectorBrain(sb.core.Brain):
    def compute_forward(self, x, stage="train", init_params=False):
        id, wavs, lens = x

        feats = hyperparams.compute_features(wavs, init_params)
        feats = hyperparams.mean_var_norm(feats, lens)
        x_vect = hyperparams.xvector_model(feats, init_params=init_params)
        outputs = hyperparams.classifier(x_vect, init_params)

        return outputs, lens

    def compute_objectives(self, predictions, targets, stage="train"):
        predictions, lens = predictions
        uttid, spkid, _ = targets

        loss = hyperparams.compute_cost(predictions, spkid, lens)

        stats = {}

        if stage != "train":
            stats["error"] = hyperparams.compute_error(predictions, spkid, lens)

        return loss, stats

    def on_epoch_end(self, epoch, train_stats, valid_stats):
        print("Epoch %d complete" % epoch)
        print("Train loss: %.2f" % summarize_average(train_stats["loss"]))
        print("Valid loss: %.2f" % summarize_average(valid_stats["loss"]))
        print("Valid error: %.2f" % summarize_average(valid_stats["error"]))


# Extracts xvector given data and truncated model
class Extractor(Sequential):
    def __init__(self, model):
        super().__init__()
        self.model = model

    def get_emb(self, feats, lens):

        emb = self.model(feats, lens)

        return emb

    def extract(self, x):
        id, wavs, lens = x

        feats = hyperparams.compute_features(wavs, init_params=False)
        feats = hyperparams.mean_var_norm(feats, lens)

        emb = self.get_emb(feats, lens)
        emb = emb.detach()

        return emb


# Data loaders
train_set = hyperparams.train_loader()
valid_set = hyperparams.valid_loader()

# Xvector Model
modules = [hyperparams.xvector_model, hyperparams.classifier]
first_x, first_y = next(iter(train_set))

# Object initialization for training xvector model
xvect_brain = XvectorBrain(
    modules=modules, optimizer=hyperparams.optimizer, first_inputs=[first_x],
)

# Train the Xvector model
xvect_brain.fit(
    range(hyperparams.number_of_epochs),
    train_set=train_set,
    valid_set=valid_set,
)
print("Xvector model training completed!")


# Instantiate extractor obj
ext_brain = Extractor(model=hyperparams.xvector_model)

# Extract xvectors from a validation sample
valid_x, valid_y = next(iter(valid_set))
print("Extracting Xvector from a sample validation batch!")
xvectors = ext_brain.extract(valid_x)
print("Extracted Xvector.Shape: ", xvectors.shape)


# Integration test: Ensure we overfit the training data
def test_error():
    assert xvect_brain.avg_train_loss < 0.1
