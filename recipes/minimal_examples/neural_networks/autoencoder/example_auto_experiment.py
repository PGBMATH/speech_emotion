#!/usr/bin/python
import os
import speechbrain as sb


class AutoBrain(sb.Brain):
    def compute_forward(self, x, stage):
        id, wavs, lens = x
        feats = self.hparams.compute_features(wavs)
        feats = self.modules.mean_var_norm(feats, lens)

        encoded = self.modules.linear1(feats)
        encoded = self.hparams.activation(encoded)
        decoded = self.modules.linear2(encoded)

        return decoded

    def compute_objectives(self, predictions, targets, stage):
        id, wavs, lens = targets
        feats = self.hparams.compute_features(wavs)
        feats = self.modules.mean_var_norm(feats, lens)
        self.mse_metric.append(id, predictions, feats, lens)
        return self.hparams.compute_cost(predictions, feats, lens)

    def fit_batch(self, batch):
        inputs = batch[0]
        predictions = self.compute_forward(inputs, sb.Stage.TRAIN)
        loss = self.compute_objectives(predictions, inputs, sb.Stage.TRAIN)
        loss.backward()
        self.optimizer.step()
        self.optimizer.zero_grad()
        return loss.detach()

    def evaluate_batch(self, batch, stage):
        inputs = batch[0]
        predictions = self.compute_forward(inputs, stage)
        loss = self.compute_objectives(predictions, inputs, stage)
        return loss.detach()

    def on_stage_start(self, stage, epoch=None):
        self.mse_metric = self.hparams.loss_tracker()

    def on_stage_end(self, stage, stage_loss, epoch=None):
        if self.hparams.use_tensorboard:
            if stage == sb.Stage.TRAIN:
                self.hparams.train_logger.log_stats(
                    {"Epoch": epoch},
                    train_stats={"loss": self.mse_metric.scores},
                )
            elif stage == sb.Stage.VALID:
                self.hparams.train_logger.log_stats(
                    {"Epoch": epoch},
                    valid_stats={"loss": self.mse_metric.scores},
                )
            if stage == sb.Stage.TEST:
                self.hparams.train_logger.log_stats(
                    {}, test_stats={"loss": self.mse_metric.scores}
                )

        if stage == sb.Stage.TRAIN:
            self.train_loss = stage_loss
        if stage == sb.Stage.VALID:
            print("Completed epoch %d" % epoch)
            print("Train loss: %.3f" % self.train_loss)
            print("Valid loss: %.3f" % stage_loss)
        if stage == sb.Stage.TEST:
            print("Test loss: %.3f" % stage_loss)


def main():
    experiment_dir = os.path.dirname(os.path.realpath(__file__))
    hparams_file = os.path.join(experiment_dir, "hyperparams.yaml")
    data_folder = "../../../../samples/audio_samples/nn_training_samples"
    data_folder = os.path.realpath(os.path.join(experiment_dir, data_folder))
    with open(hparams_file) as fin:
        hparams = sb.load_extended_yaml(fin, {"data_folder": data_folder})

    if hparams["use_tensorboard"]:
        from speechbrain.utils.train_logger import TensorboardLogger

        train_logger = TensorboardLogger(hparams["tensorboard_logs"])
        hparams["hparams"]["train_logger"] = train_logger

    auto_brain = AutoBrain(hparams["modules"], hparams["opt_class"], hparams)
    auto_brain.fit(
        range(hparams["N_epochs"]),
        hparams["train_loader"](),
        hparams["valid_loader"](),
    )
    auto_brain.evaluate(hparams["test_loader"]())

    # Check that model overfits for integration test
    assert auto_brain.train_loss < 0.08


if __name__ == "__main__":
    main()


def test_loss():
    main()
