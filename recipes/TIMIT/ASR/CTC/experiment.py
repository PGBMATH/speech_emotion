#!/usr/bin/env python3
"""Recipe for doing ASR with phoneme targets and CTC loss on the TIMIT dataset

To run this recipe, do the following:
> python experiment.py hyperparams.yaml --data_folder /path/to/TIMIT

Authors
 * Mirco Ravanelli 2020
 * Peter Plantinga 2020
 * Samuele Cornell 2020
 * Aku Rouhe 2020
"""
import logging
import os
import sys
import torch
import speechbrain as sb
from speechbrain.data_io.encoders import CTCTextEncoder
from speechbrain.utils.data_utils import split_by_whitespace
from speechbrain.data_io.data_io import load_json
from speechbrain.data_io.legacy import csv_to_json

logger = logging.getLogger(__name__)


# Define training procedure
class ASR_Brain(sb.Brain):
    def compute_forward(self, batch, stage):
        wavs, wav_lens = batch["wav"]
        wavs, wav_lens = wavs.to(self.device), wav_lens.to(self.device)

        # Adding augmentation when specified:
        if stage == sb.Stage.TRAIN:
            if hasattr(self.modules, "env_corrupt"):
                wavs_noise = self.modules.env_corrupt(wavs, wav_lens)
                wavs = torch.cat([wavs, wavs_noise], dim=0)
                wav_lens = torch.cat([wav_lens, wav_lens])
            if hasattr(self.hparams, "augmentation"):
                wavs = self.hparams.augmentation(wavs, wav_lens)

        feats = self.hparams.compute_features(wavs)
        feats = self.modules.normalize(feats, wav_lens)
        out = self.modules.model(feats)
        out = self.modules.output(out)
        pout = self.hparams.log_softmax(out)

        return pout, wav_lens

    def compute_objectives(self, predictions, batch, stage):
        pout, pout_lens = predictions
        ids = batch["id"]
        phns, phn_lens = batch["phn"]
        phns, phn_lens = phns.to(self.device), phn_lens.to(self.device)

        if stage == sb.Stage.TRAIN and hasattr(self.modules, "env_corrupt"):
            phns = torch.cat([phns, phns], dim=0)
            phn_lens = torch.cat([phn_lens, phn_lens], dim=0)

        loss = self.hparams.compute_cost(pout, phns, pout_lens, phn_lens)
        self.ctc_metrics.append(ids, pout, phns, pout_lens, phn_lens)

        if stage != sb.Stage.TRAIN:
            sequence = sb.decoders.ctc_greedy_decode(
                pout, pout_lens, blank_id=self.hparams.blank_index
            )
            self.per_metrics.append(
                ids,
                sequence,
                phns,
                None,
                phn_lens,
                self.hparams.label_encoder.decode_int,
            )

        return loss

    def on_stage_start(self, stage, epoch):
        self.ctc_metrics = self.hparams.ctc_stats()

        if stage != sb.Stage.TRAIN:
            self.per_metrics = self.hparams.per_stats()

    def on_stage_end(self, stage, stage_loss, epoch):
        if stage == sb.Stage.TRAIN:
            self.train_loss = stage_loss
        else:
            per = self.per_metrics.summarize("error_rate")

        if stage == sb.Stage.VALID:
            old_lr, new_lr = self.hparams.lr_annealing(per)
            sb.nnet.schedulers.update_learning_rate(self.optimizer, new_lr)

            # In distributed setting, only want to save model/stats once
            if self.root_process:
                self.hparams.train_logger.log_stats(
                    stats_meta={"epoch": epoch, "lr": old_lr},
                    train_stats={"loss": self.train_loss},
                    valid_stats={"loss": stage_loss, "PER": per},
                )
                self.checkpointer.save_and_keep_only(
                    meta={"PER": per}, min_keys=["PER"],
                )

        elif stage == sb.Stage.TEST:
            self.hparams.train_logger.log_stats(
                stats_meta={"Epoch loaded": self.hparams.epoch_counter.current},
                test_stats={"loss": stage_loss, "PER": per},
            )
            with open(self.hparams.wer_file, "w") as w:
                w.write("CTC loss stats:\n")
                self.ctc_metrics.write_stats(w)
                w.write("\nPER stats:\n")
                self.per_metrics.write_stats(w)
                print("CTC and PER stats written to ", self.hparams.wer_file)


# Begin Recipe!
if __name__ == "__main__":

    # This hack needed to import data preparation script from ..
    current_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.append(os.path.dirname(os.path.dirname(current_dir)))
    from timit_prepare import prepare_timit  # noqa E402

    # Load hyperparameters file with command-line overrides
    hparams_file, overrides, args = sb.parse_arguments(sys.argv[1:])

    if args["stage"] <= 0:

        logger.info("Preparing data")
        prepare_timit(
            data_folder=args["data_folder"],
            splits=["train", "dev", "test"],
            save_folder=args["data_folder"],
        )

        for csv in ["train.csv", "dev.csv", "test.csv"]:
            csv_to_json(os.path.join(args["data_folder"], csv))

    if args["stage"] <= 1:

        logger.info("Preparing label dictionary")
        train_data = load_json(os.path.join(args["data_folder"], "train.json"))
        valid_data = load_json(os.path.join(args["data_folder"], "dev.json"))

        encoder = CTCTextEncoder()
        encoder.fit([train_data, valid_data], "phn", split_by_whitespace)
        encoder.save("encoder_state.pkl")

    if args["stage"] <= 2:

        logger.info("Training System")
        with open(hparams_file) as fin:
            hparams = sb.load_extended_yaml(fin, overrides)

        # Create experiment directory
        sb.create_experiment_directory(
            experiment_directory=hparams["output_folder"],
            hyperparams_to_save=hparams_file,
            overrides=overrides,
        )

        asr_brain = ASR_Brain(
            hparams["modules"],
            hparams["opt_class"],
            hparams,
            checkpointer=hparams["checkpointer"],
        )
        asr_brain.fit(
            asr_brain.hparams.epoch_counter,
            hparams["train_loader"],
            hparams["valid_loader"],
        )

        asr_brain.evaluate(hparams["test_loader"], min_key="PER")
