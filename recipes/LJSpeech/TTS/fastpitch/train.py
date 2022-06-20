"""
 Recipe for training the Fastpitch Text-To-Speech model, an end-to-end
 neural text-to-speech (TTS) system introduced in 'Fastpitch: Parallel Text-to-Speech with Pitch Prediction' paper published in ICASSP 2021.
 (https://ieeexplore.ieee.org/document/9413889)
 To run this recipe, do the following:
 # python train.py hparams/train.yaml

 Authors
 * Sathvik Udupa 2022
"""

import os, sys
import torch
import logging
import torchaudio
import numpy as np
from pathlib import Path
from hyperpyyaml import load_hyperpyyaml

sys.path.append("../../../../") #remove

import speechbrain as sb
from speechbrain.pretrained import HIFIGAN

logger = logging.getLogger(__name__)

class FastPitchBrain(sb.Brain):

    def on_fit_start(self):
        """Gets called at the beginning of ``fit()``, on multiple processes
        if ``distributed_count > 0`` and backend is ddp and initializes statistics"""
        self.hparams.progress_sample_logger.reset()
        self.last_epoch = 0
        self.last_batch = None
        self.last_loss_stats = {}
        return super().on_fit_start()

    def compute_forward(self, batch, stage):
        """Computes the forward pass
        Arguments
        ---------
        batch: str
            a single batch
        stage: speechbrain.Stage
            the training stage
        Returns
        -------
        the model output
        """
        inputs, _ = batch_to_gpu(batch)
        return self.hparams.model(*inputs)

    def fit_batch(self, batch):
        """Fits a single batch
        Arguments
        ---------
        batch: tuple
            a training batch
        Returns
        -------
        loss: torch.Tensor
            detached loss
        """
        result = super().fit_batch(batch)
        return result

    def compute_objectives(self, predictions, batch, stage):
        """Computes the loss given the predicted and targeted outputs.
        Arguments
        ---------
        predictions : torch.Tensor
            The model generated spectrograms and other metrics from `compute_forward`.
        batch : PaddedBatch
            This batch object contains all the relevant tensors for computation.
        stage : sb.Stage
            One of sb.Stage.TRAIN, sb.Stage.VALID, or sb.Stage.TEST.
        Returns
        -------
        loss : torch.Tensor
            A one-element tensor used for backpropagating the gradient.
        """
        x, y, metadata = batch_to_gpu(batch, return_metadata=True)
        self.last_batch = [x[0], y[-1], predictions[0], *metadata]
        self._remember_sample([x[0], *y, *metadata], predictions)
        return self.hparams.criterion(predictions, y)

    def _remember_sample(self, batch, predictions):
        """Remembers samples of spectrograms and the batch for logging purposes
        Arguments
        ---------
        batch: tuple
            a training batch
        predictions: tuple
            predictions (raw output of the FastPitch model)
        """
        tokens, spectogram, durations, pitch, mel_lengths, input_lengths, labels, wavs = batch
        mel_post, predict_durations, predicted_pitch, token_level_pitch = predictions
        self.hparams.progress_sample_logger.remember(
            target=self.process_mel(spectogram, mel_lengths),
            output=self.process_mel(mel_post, mel_lengths),
            raw_batch=self.hparams.progress_sample_logger.get_batch_sample(
                {
                    "tokens": tokens,
                    "input_lengths": input_lengths,
                    "mel_target": spectogram,
                    "mel_out": mel_post,
                    "mel_lengths": mel_lengths,
                    "durations": durations,
                    "predict_durations": predict_durations,
                    "labels": labels,
                    "wavs": wavs

                }
            ),
        )





    def process_mel(self, mel, len, index=0):
        """Converts a mel spectrogram to one that can be saved as an image
        sample  = sqrt(exp(mel))
        Arguments
        ---------
        mel: torch.Tensor
            the mel spectrogram (as used in the model)
        len: int
            length of the mel spectrogram
        index: int
            batch index
        Returns
        -------
        mel: torch.Tensor
            the spectrogram, for image saving purposes
        """
        assert mel.dim() == 3
        return torch.sqrt(torch.exp(mel[index][:len[index]]))


    def on_stage_end(self, stage, stage_loss, epoch):
        """Gets called at the end of an epoch.
        Arguments
        ---------
        stage : sb.Stage
            One of sb.Stage.TRAIN, sb.Stage.VALID, sb.Stage.TEST
        stage_loss : float
            The average loss for all of the data processed in this stage.
        epoch : int
            The currently-starting epoch. This is passed
            `None` during the test stage.
        """
        # Store the train loss until the validation stage.
        if stage == sb.Stage.TRAIN:
            self.train_loss = stage_loss
        # Summarize the statistics from the stage for record-keeping.
        else:
            stats = {
                "loss": stage_loss,
            }

        # At the end of validation, we can write
        if stage == sb.Stage.VALID:
            # Update learning rate
            self.last_epoch = epoch
            lr = self.optimizer.param_groups[-1]["lr"]

            # The train_logger writes a summary to stdout and to the logfile.
            self.hparams.train_logger.log_stats(  # 1#2#
                stats_meta={"Epoch": epoch, "lr": lr},
                train_stats={"loss": self.train_loss},
                valid_stats=stats,
            )
            output_progress_sample = (
                self.hparams.progress_samples
                and epoch % self.hparams.progress_samples_interval == 0
                and epoch >= self.hparams.progress_samples_min_run
            )

            if output_progress_sample:
                logger.info('Saving predicted samples')
                self.run_inference()

                self.hparams.progress_sample_logger.save(epoch)
                self.run_vocoder()
            # Save the current checkpoint and delete previous checkpoints.
            #UNCOMMENT THIS
            self.checkpointer.save_and_keep_only(meta=stats, min_keys=["loss"])
        # We also write statistics about test data spectogramto stdout and to the logfile.
        if stage == sb.Stage.TEST:
            self.hparams.train_logger.log_stats(
                {"Epoch loaded": self.hparams.epoch_counter.current},
                test_stats=stats,
            )
    def run_inference(self, index=0):
        """Produces a sample in inference mode with predicted durations.
        Arguments
        ---------
        index: int
            batch index
        """
        if self.last_batch is None:
            return
        tokens, input_lengths, *_ = self.last_batch
        token = tokens[index][:input_lengths[index]]
        print(token.shape)
        mel_post, *_ =  self.hparams.model(token.unsqueeze(0))
        self.hparams.progress_sample_logger.remember(
            infer_output=self.process_mel(mel_post, [len(mel_post[0])])
        )

    def run_vocoder(self):
        """Uses a pretrained vocoder  to generate audio from predicted mel
        spectogram. By default, uses speechbrain hifigan."""

        if self.last_batch is None:
            return
        _, _, mel, _, wavs = self.last_batch
        mel = mel[:self.hparams.progress_batch_sample_size]
        assert self.hparams.vocoder == 'hifi-gan' and self.hparams.pretrained_vocoder is True, 'Specified vocoder not supported yet'
        logger.info(f'Generating audio with pretrained {self.hparams.vocoder_source} vocoder')
        hifi_gan = HIFIGAN.from_hparams(source=self.hparams.vocoder_source, savedir=self.hparams.vocoder_download_path)
        waveforms = hifi_gan.decode_batch(mel.transpose(2, 1))
        for idx, wav in enumerate(waveforms):

            path = os.path.join(self.hparams.progress_sample_path, str(self.last_epoch),  f"pred_{Path(wavs[idx]).stem}.wav")
            torchaudio.save(path, wav, self.hparams.sample_rate)

def dataio_prepare(hparams):
    #read saved lexicon
    with open(os.path.join(hparams["save_folder"], "lexicon"), 'r') as f:
        lexicon = f.read().split('\t')
    input_encoder = hparams.get("input_encoder")

    #add a dummy symbol for idx 0 - used for padding.
    lexicon = ['@@'] + lexicon
    input_encoder.update_from_iterable(
                lexicon,
                sequence_input=False)
    #load audio, text and durations on the fly; encode audio and text.
    @sb.utils.data_pipeline.takes("wav", "label", "durations")
    @sb.utils.data_pipeline.provides("mel_text_pair")
    def audio_pipeline(wav, label, dur):
        durs = np.load(dur)
        durs_seq = torch.from_numpy(durs).int()
        text_seq = input_encoder.encode_sequence_torch(label.lower()).int()
        assert len(text_seq) == len(durs) #ensure every token has a duration
        audio = sb.dataio.dataio.read_audio(wav)
        mel = hparams["mel_spectogram"](audio=audio)
        pitch = torchaudio.functional.compute_kaldi_pitch(waveform=audio,
                                                        sample_rate=hparams["sample_rate"],
                                                        frame_length=(hparams["n_fft"] / hparams["sample_rate"] * 1000),
                                                        frame_shift=(hparams["hop_length"] / hparams["sample_rate"] * 1000),
                                                        min_f0=hparams["min_f0"],
                                                        max_f0=hparams["max_f0"],
                                                        )[:, 0]
        pitch = pitch[:mel.shape[-1]]
        return text_seq, durs_seq, mel, pitch, len(text_seq)

    #define splits and load it as sb dataset
    datasets = {}

    for dataset in hparams["splits"]:
        datasets[dataset] = sb.dataio.dataset.DynamicItemDataset.from_json(
            json_path=hparams[f"{dataset}_json"],
            replacements={"data_root": hparams["data_folder"]},
            dynamic_items=[audio_pipeline],
            output_keys=["mel_text_pair", "wav", "label", "durations"],
        )
    return datasets


def batch_to_gpu(batch, return_metadata=False):
    """Transfers the batch to the target device
        Arguments
        ---------
        batch: tuple
            the batch to use
        Returns
        -------
        batch: tuple
            the batch on the correct device
        """

    (
        text_padded,
        durations,
        input_lengths,
        mel_padded,
        pitch_padded,
        output_lengths,
        len_x,
        labels,
        wavs
    ) = batch

    durations = to_gpu(durations).long()
    phonemes = to_gpu(text_padded).long()
    input_lengths = to_gpu(input_lengths).long()
    max_len = torch.max(input_lengths.data).item()
    spectogram = to_gpu(mel_padded).float()
    pitch = to_gpu(pitch_padded).float()
    mel_lengths = to_gpu(output_lengths).long()
    x = (phonemes, durations, pitch)
    y = (spectogram, durations, pitch, mel_lengths, input_lengths)
    metadata = (labels, wavs)
    if return_metadata:
        return x, y, metadata
    return x, y

def to_gpu(x):
    x = x.contiguous()

    if torch.cuda.is_available():
        x = x.cuda(non_blocking=True)
    return x

def main():
    hparams_file, run_opts, overrides = sb.parse_arguments(sys.argv[1:])
    with open(hparams_file) as fin:
        hparams = load_hyperpyyaml(fin, overrides)
    sb.utils.distributed.ddp_init_group(run_opts)

    sb.create_experiment_directory(
        experiment_directory=hparams["output_folder"],
        hyperparams_to_save=hparams_file,
        overrides=overrides,
    )

    sys.path.append("../")
    from ljspeech_prepare import prepare_ljspeech
    sb.utils.distributed.run_on_main(
        prepare_ljspeech,
        kwargs={
            "data_folder": hparams["data_folder"],
            "save_folder": hparams["save_folder"],
            "duration_link": hparams["duration_link"],
            "splits": hparams["splits"],
            "split_ratio": hparams["split_ratio"],
            "seed": hparams["seed"],
            "skip_prep": hparams["skip_prep"],
            "create_symbol_list": True,
        },
    )
    datasets = dataio_prepare(hparams)

    # Brain class initialization
    fastpitch_brain = FastPitchBrain(
        modules=hparams["modules"],
        opt_class=hparams["opt_class"],
        hparams=hparams,
        run_opts=run_opts,
        checkpointer=hparams["checkpointer"],
    )
    # Training
    fastpitch_brain.fit(
        fastpitch_brain.hparams.epoch_counter,
        datasets["train"],
        datasets["valid"],
        train_loader_kwargs=hparams["train_dataloader_opts"],
        valid_loader_kwargs=hparams["valid_dataloader_opts"],
    )

   

if __name__ == "__main__":
    main()
