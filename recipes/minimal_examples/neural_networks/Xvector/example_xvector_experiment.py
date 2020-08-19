#!/usr/bin/python
import os
import speechbrain as sb
from speechbrain.nnet.containers import Sequential


# Trains xvector model
class XvectorBrain(sb.Brain):
    def compute_forward(self, x, stage=sb.Stage.TRAIN, init_params=False):
        id, wavs, lens = x

        feats = self.compute_features(wavs, init_params)
        feats = self.mean_var_norm(feats, lens)
        x_vect = self.xvector_model(feats, init_params=init_params)
        outputs = self.classifier(x_vect, init_params)

        return outputs, lens

    def compute_objectives(self, predictions, targets, stage=sb.Stage.TRAIN):
        predictions, lens = predictions
        uttid, spkid, _ = targets

        loss = self.compute_cost(predictions, spkid, lens)

        if stage != sb.Stage.TRAIN:
            self.error_metrics.append(uttid, predictions, spkid, lens)

        return loss

    def on_stage_start(self, stage, epoch=None):
        if stage != sb.Stage.TRAIN:
            self.error_metrics = self.error_stats()

    def on_stage_end(self, stage, stage_loss, epoch=None):
        if stage == sb.Stage.TRAIN:
            self.train_loss = stage_loss
        if stage == sb.Stage.VALID:
            print("Epoch %d complete" % epoch)
            print("Train loss: %.2f" % self.train_loss)
        if stage != sb.Stage.TRAIN:
            print(stage, "loss: %.2f" % stage_loss)
            print(
                stage, "error: %.2f" % self.error_metrics.summarize("average")
            )


# Extracts xvector given data and truncated model
class Extractor(Sequential):
    def __init__(self, model, feats, norm):
        super().__init__()
        self.model = model
        self.feats = feats
        self.norm = norm

    def get_emb(self, feats, lens):

        emb = self.model(feats, lens)

        return emb

    def extract(self, x):
        id, wavs, lens = x

        feats = self.feats(wavs, init_params=False)
        feats = self.norm(feats, lens)

        emb = self.get_emb(feats, lens)
        emb = emb.detach()

        return emb


def main():
    # Load hyperparams file
    experiment_dir = os.path.dirname(os.path.abspath(__file__))
    hyperparams_file = os.path.join(experiment_dir, "hyperparams.yaml")
    data_folder = "../../../../../samples/voxceleb_samples/wav/"
    data_folder = os.path.abspath(experiment_dir + data_folder)

    with open(hyperparams_file) as fin:
        hyperparams = sb.load_extended_yaml(fin, {"data_folder": data_folder})

    # Data loaders
    train_set = hyperparams.train_loader()
    valid_set = hyperparams.valid_loader()

    # Xvector Model
    first_x, first_y = next(iter(train_set))

    # Object initialization for training xvector model
    xvect_brain = XvectorBrain(
        modules=hyperparams.modules,
        optimizers={("xvector_model", "classifier"): hyperparams.optimizer},
        device="cpu",
        first_inputs=[first_x],
    )

    # Train the Xvector model
    xvect_brain.fit(
        range(hyperparams.number_of_epochs),
        train_set=train_set,
        valid_set=valid_set,
    )
    print("Xvector model training completed!")

    # Instantiate extractor obj
    ext_brain = Extractor(
        model=hyperparams.modules["xvector_model"],
        feats=hyperparams.modules["compute_features"],
        norm=hyperparams.modules["mean_var_norm"],
    )

    # Extract xvectors from a validation sample
    valid_x, valid_y = next(iter(valid_set))
    print("Extracting Xvector from a sample validation batch!")
    xvectors = ext_brain.extract(valid_x)
    print("Extracted Xvector.Shape: ", xvectors.shape)

    # Check that the model overfits for an integration test
    assert xvect_brain.train_loss < 0.1


if __name__ == "__main__":
    main()


def test_error():
    main()
