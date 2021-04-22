import librosa
import torch
import sys
import speechbrain as sb
import math
from typing import Collection
from torch.nn import functional as F
from hyperpyyaml import load_hyperpyyaml
from speechbrain.dataio.dataset import DynamicItemDataset
from speechbrain.dataio.dataloader import SaveableDataLoader
from torch.utils.data import DataLoader
import numpy as np
import os

import torchvision
import torchaudio

sys.path.append("..")
from datasets.vctk import VCTK
from common.dataio import audio_pipeline, mel_spectrogram, spectrogram, resample

from scipy.signal import firwin, lfilter


class WavenetBrain(sb.core.Brain):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def compute_forward(self, batch, stage, use_targets=True):

        batch = batch.to(self.device)#BatchWrapper(batch).to(self.device)
        
        pred = self.hparams.model(
            x=batch.sig_quantized.data
        )

        return pred

    def compute_objectives(self, predictions, batch, stage):
        """Computes the loss given the predicted and targeted outputs.
        Arguments
        ---------
        predictions : torch.Tensor
            The posterior probabilities from `compute_forward`.
        batch : PaddedBatch
            This batch object contains all the relevant tensors for computation.
        stage : sb.Stage
            One of sb.Stage.TRAIN, sb.Stage.VALID, or sb.Stage.TEST.
        Returns
        -------
        loss : torch.Tensor
            A one-element tensor used for backpropagating the gradient.
        """
        #batch = BatchWrapper(batch).to(self.device)
        
        # wee need 4d inputs for spatial cross entropy loss
        # (B, T, C, 1)
        y_hat = predictions.unsqueeze(-1)
        target = batch.target.data.unsqueeze(-1)
        lengths = batch.input_lengths

        loss = self.hparams.compute_cost(
            y_hat[:,:-1,:,:], target[:,1:,:], lengths=lengths
        )
        # (B, T)

        y_hat = F.softmax(y_hat.squeeze(), dim=1).max(1)[1]

        predicted_audio = inv_mulaw_quantize(y_hat)
        target_audio = inv_mulaw_quantize(target.squeeze())

        predicted_mel = torchaudio.transforms.MelSpectrogram(self.hparams.sample_rate)(predicted_audio)
        target_mel = torchaudio.transforms.MelSpectrogram(self.hparams.sample_rate)(target_audio)

        (self.last_predicted_audio, 
         self.last_target_audio, 
         self.last_predicted_mel, 
         self.last_target_mel) = [
            tensor.detach().cpu()
            for tensor in (
                predicted_audio, target_audio,
                predicted_mel, target_mel
            )]

        return loss

    def _save_progress_sample(self):
        self._save_sample_audio(
            'target_audio.wav', self.last_target_audio.unsqueeze(0))
        self._save_sample_audio(
            'predicted_audio.wav', self.last_predicted_audio.unsqueeze(0))
        self._save_sample_image(
            'target_mel.png', self.last_target_mel)
        print(self.last_predicted_mel.size())
        self._save_sample_image(
            'output_mel.png', self.last_predicted_mel)

    def _save_sample_image(self, file_name, data):
        effective_file_name = os.path.join(self.hparams.progress_sample_path, file_name)
        torchvision.utils.save_image(data, effective_file_name)

    def _save_sample_audio(self, file_name, data):
        effective_file_name = os.path.join(self.hparams.progress_sample_path, file_name)
        torchaudio.save(effective_file_name,data, sample_rate=self.hparams.sample_rate)

    def on_fit_start(self):
        super().on_fit_start()
        if self.hparams.progress_samples:
            if not os.path.exists(self.hparams.progress_sample_path):
                os.makedirs(self.hparams.progress_sample_path)

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
            old_lr, new_lr = self.hparams.lr_annealing(stage_loss)
            sb.nnet.schedulers.update_learning_rate(self.optimizer, new_lr)

            # The train_logger writes a summary to stdout and to the logfile.
            self.hparams.train_logger.log_stats(
                {"Epoch": epoch},
                train_stats={"loss": self.train_loss},
                valid_stats=stats,
            )

            # Save the current checkpoint and delete previous checkpoints.
            self.checkpointer.save_and_keep_only(meta=stats, min_keys=["loss"])
            output_progress_sample =(
                self.hparams.progress_samples
                and epoch % self.hparams.progress_samples_interval == 0)
            if output_progress_sample:
                self._save_progress_sample() 

        # We also write statistics about test data to stdout and to the logfile.
        if stage == sb.Stage.TEST:
            self.hparams.train_logger.log_stats(
                {"Epoch loaded": self.hparams.epoch_counter.current},
                test_stats=stats,
            )

class BatchWrapper:
    def __init__(self, batch):
        self.batch = batch

    def to(self, device):
        for key, value in self.batch.items():
            if hasattr(value, 'to'):
                self.batch[key] = value.to(device)
        return self
    
    def __getattr__(self, name):
        return self.batch[name]

class SingleBatchWrapper(DataLoader):
    """
    A wrapper that retrieves one batch from a DataLoader
    and keeps iterating - useful for overfit tests
    """
    def __init__(self, loader: DataLoader, num_iterations=1):
        """
        Class constructor
        
        Arguments
        ---------
        loader
            the inner data loader
        """
        self.loader = loader
        self.num_iterations = num_iterations

    def __iter__(self):
        batch = next(iter(self.loader))
        for _ in range(self.num_iterations):
            yield batch

#TODO: Remove the librosa dependency
def trim(takes, provides, top_db=15):
    @sb.utils.data_pipeline.takes(takes)
    @sb.utils.data_pipeline.provides(provides)    
    def f(wav):
        x, _ = librosa.effects.trim(wav, top_db=top_db)
        #print("CONSTRUCT ERROR")
        #print(type(x))
        #x = torch.tensor(x).to(wav.device)
        return x
    return f

def low_cut_filter(takes,provides,fs,cutoff):
    @sb.utils.data_pipeline.takes(takes)
    @sb.utils.data_pipeline.provides(provides)

    def f(x):
        """APPLY LOW CUT FILTER.
        https://github.com/kan-bayashi/PytorchWaveNetVocoder

        Args:
            x (ndarray): Waveform sequence.
            fs (int): Sampling frequency.
            cutoff (float): Cutoff frequency of low cut filter.
        Return:
            ndarray: Low cut filtered waveform sequence.
        """
        if cutoff > 0.0:
            nyquist = fs // 2
            norm_cutoff = cutoff / nyquist
            
            # low cut filter
            fil = firwin(255, norm_cutoff, pass_zero=False)
            lcf_x = lfilter(fil, 1, x)

            return torch.from_numpy(lcf_x)
        else:
            return x

    return f

def mulaw(x, mu=256):
    """Mu-Law companding
    Method described in paper [1]_.
    .. math::
        f(x) = sign(x) \ln (1 + \mu |x|) / \ln (1 + \mu)
    Args:
        x (array-like): Input signal. Each value of input signal must be in
          range of [-1, 1].
        mu (number): Compression parameter ``μ``.
    Returns:
        array-like: Compressed signal ([-1, 1])

    .. [1] Brokish, Charles W., and Michele Lewis. "A-law and mu-law companding
        implementations using the tms320c54x." SPRA163 (1997).
    """
    product = mu*x.abs()
    return x.sign() * product.log1p() / np.log1p(mu)

def mulaw_quantize(x, mu=256):
    """Mu-Law companding + quantize
    Args:
        x (array-like): Input signal. Each value of input signal must be in
          range of [-1, 1].
        mu (number): Compression parameter ``μ``.
    Returns:
        array-like: Quantized signal (dtype=int)
          - y ∈ [0, mu] if x ∈ [-1, 1]
          - y ∈ [0, mu) if x ∈ [-1, 1)
    .. note::
        If you want to get quantized values of range [0, mu) (not [0, mu]),
        then you need to provide input signal of range [-1, 1).
    """
    y = mulaw(x, mu)
    # scale [-1, 1] to [0, mu]
    return ((y + 1) / 2 * mu).long()

def inv_mulaw(y, mu=256):
    return y.sign() * (1.0 / mu) * ((1.0 + mu)**y.abs() - 1.0)

def inv_mulaw_quantize(y,mu=256):
    y = 2*y.type(torch.FloatTensor)/mu -1
    return inv_mulaw(y,mu)

def start_and_end_indices(quantized, silence_threshold=2):

    for start in range(quantized.size(0)):
        if abs(quantized[start] - 127) > silence_threshold:
            break
    for end in range(quantized.size(0) - 1, 1, -1):
        if abs(quantized[end] - 127) > silence_threshold:
            break

    assert abs(quantized[start] - 127) > silence_threshold
    assert abs(quantized[end] - 127) > silence_threshold

    return start, end

def mulaw_trim(takes,provides,silence_threshold,is_quantized):
    # trim silence in mu-law quantized domain
    @sb.utils.data_pipeline.takes(takes)
    @sb.utils.data_pipeline.provides(provides)

    def f(wav):
        if is_quantized:
            if silence_threshold > 0:
                # [0, quantize_channels)
                out = mulaw_quantize(wav, 255)
                start, end = start_and_end_indices(out, silence_threshold)
                wav = wav[start:end]
        return wav.float()

    return f

def wav_clip(takes,provides):
    # Clip
    @sb.utils.data_pipeline.takes(takes)
    @sb.utils.data_pipeline.provides(provides)

    def f(wav):
        return torch.clip(wav, -1.0, 1.0)

    return f

def mulaw_target(takes,provides,is_quantized):
    # return target signal under a mulaw transformation
    @sb.utils.data_pipeline.takes(takes)
    @sb.utils.data_pipeline.provides(provides)

    def f(wav):
        if is_quantized:
            out = mulaw_quantize(wav, 255)
        else:
            out = mulaw(wav, 255)
        return out

    return f

def zero_pad(takes, provides,n_fft, is_quantized):
    # zero pad
    # this is needed to adjust time resolution between audio and mel-spectrogram
    @sb.utils.data_pipeline.takes(takes)
    @sb.utils.data_pipeline.provides(provides)

    def f(wav):
        if is_quantized:
            constant_values = mulaw_quantize(torch.tensor(0.0), 255)
        else:
            constant_values = mulaw(torch.tensor(0.0), 255)
        
        l, r = (0,n_fft)
        if l > 0 or r > 0:
            out = F.pad(wav, (l, r), mode="constant", value=constant_values)
        return out

    return f

LOG_10 = math.log(10)

def normalize_spectrogram(takes, provides, min_level_db, ref_level_db, absolute=False):
    @sb.utils.data_pipeline.takes(takes)
    @sb.utils.data_pipeline.provides(provides)
    def f(linear):
        if absolute:
            linear = (linear**2).sum(dim=-1).sqrt()
        min_level = torch.tensor(math.exp(min_level_db / ref_level_db * LOG_10)).to(linear.device)
        linear_db = ref_level_db * torch.log10(torch.maximum(min_level, linear)) - ref_level_db
        normalized = torch.clip(
            (linear_db - min_level_db) / -min_level_db,
            min=0.,
            max=1.
        )
        return normalized

    return f


def ensure_divisible(length, divisible_by=256, lower=True):
    if length % divisible_by == 0:
        return length
    if lower:
        return length - length % divisible_by
    else:
        return length + (divisible_by - length % divisible_by)

def time_resolution(takes, provides, max_time_steps, hop_length):
    @sb.utils.data_pipeline.takes(takes)
    @sb.utils.data_pipeline.provides(provides)
    def f(sig_padded):
        x = sig_padded # ADD LOCAL AND GLOBAL CONDITIONS
        

        if max_time_steps is not None and len(x) > max_time_steps:
            x = x[0:max_time_steps]
            '''
            max_steps = ensure_divisible(x.size(0), hop_length, True)
            max_time_frames = max_steps // hop_length
            print(hop_length,max_steps,max_time_frames)
            s = np.random.randint(0, len(x) - max_time_steps)
            ts = s*hop_length
            x = x[ts:ts + hop_length * max_time_frames]
            '''

        
        return x
    return f

'''
@sb.utils.data_pipeline.provides("local")
def local_conditioning(c):
    if c==-1:
        return None


@sb.utils.data_pipeline.provides("global")
def global_conditioning(g):
    if g==-1:
        return None
'''

@sb.utils.data_pipeline.takes("sig")
@sb.utils.data_pipeline.provides("input_lengths")
def target_lengths(sig):
    return sig.size(0)


def to_categorical(takes, provides, num_classes=None, dtype='float32'):
    @sb.utils.data_pipeline.takes("sig")
    @sb.utils.data_pipeline.provides("sig_quantized")
    def f(sig):
        """
        Converts a class vector (integers) to 1-hot encodes a tensor
        """ 
        return torch.from_numpy(np.asarray(np.eye(num_classes, dtype='float32')[sig]))
    return f

# find minimum timescale to scale uniformly audio to
def min_time(files_path):
    wav_files=[]
    for path, subdirs, files in os.walk(files_path):
        for name in files:
            if name.endswith("wav"):
                wav_files.append(os.path.join(path,name))
    time_scales=[]
    for w in wav_files:
        audio = sb.dataio.dataio.read_audio(w)
        time_scales.append


OUTPUT_KEYS = ["wav","sig","sig_quantized","mel_raw","mel_norm","target","input_lengths"]

def dataset_prep(dataset:DynamicItemDataset, hparams, tokens=None):
    """
    Prepares one or more datasets for use with wavenet.
    In order to be usable with the Wavenet model, a dataset needs to contain
    the following keys
    'wav': a file path to a .wav file containing the utterance

    Arguments
    ---------
    datasets
        a collection or datasets
    
    Returns
    -------
    the original dataset enhanced
    """
    # preprocess.py pipeline from r9y9 implementation
    pipeline = [
        audio_pipeline,
        # remove leading and trailing silence
        trim(
            takes="sig", 
            provides="sig_trimmed"),
        low_cut_filter(
            takes = "sig_trimmed", 
            provides="sig_cut", 
            fs = hparams["sample_rate"],
            cutoff=hparams["highpass_cutoff"]),
        mulaw_trim(
            takes = "sig_cut",
            provides = "sig_silence_trim",
            silence_threshold = hparams["silence_threshold"],
            is_quantized = hparams["is_mulaw_quantized"]),
        mel_spectrogram(
            takes="sig_silence_trim",
            provides="mel_raw",
            hop_length=hparams['hop_length'],
            n_mels=hparams['mel_dim'],
            n_fft=hparams['n_fft'],
            power=1,
            sample_rate=hparams['sample_rate']),
        wav_clip(
            takes = "sig_silence_trim",
            provides = "target_full"),
        mulaw_target(
            takes= "target_full", 
            provides="sig_mulaw", 
            is_quantized = hparams["is_mulaw_quantized"]),
        zero_pad(
            takes = "sig_mulaw",
            provides = "sig_padded",
            n_fft = hparams["n_fft"],
            is_quantized = hparams["is_mulaw_quantized"]),
        normalize_spectrogram(
            takes="mel_raw",
            provides="mel_norm",
            min_level_db=hparams['min_level_db'],
            ref_level_db=hparams['ref_level_db']),
        time_resolution(
            takes = "sig_padded",
            provides = "sig",
            max_time_steps= hparams["max_time_steps"],
            hop_length= hparams["hop_length"]
        ),
        time_resolution(
            takes = "sig_mulaw",
            provides = "target",
            max_time_steps= hparams["max_time_steps"],
            hop_length= hparams["hop_length"]
        ),
        to_categorical(
            takes = "sig",
            provides = "sig_quantized",
            num_classes = hparams["quantize_channels"]
        ),
        target_lengths
    ]

    '''
        local_conditioning(
            provides="local",
            c = hparams["c"]
        ),
        global_conditioning(
            provides="global",
            g = hparams["g"]
        )
    '''

    for element in pipeline:
        dataset.add_dynamic_item(element)

    dataset.set_output_keys(OUTPUT_KEYS)

    return dataset

def dataio_prep(hparams):
    result = {}
    for name, dataset_params in hparams['datasets'].items():
        # TODO: Add support for multiple datasets by instantiating from hparams - this is temporary
        vctk = VCTK(dataset_params['path']).to_dataset()
        result[name] = dataset_prep(vctk,hparams)
    return result


def main():

    hparams_file, run_opts, overrides = sb.parse_arguments(sys.argv[1:])
    with open(hparams_file) as fin:
        hparams = load_hyperpyyaml(fin, overrides)

    # Create experiment directory
    sb.create_experiment_directory(
        experiment_directory=hparams["output_folder"],
        hyperparams_to_save=hparams_file,
        overrides=overrides,
    )
    # Create dataset objects "train", "valid", and "test".

    #min_time(hparams["datasets"]["train"]["path"])
    datasets = dataio_prep(hparams)
    '''
    print(type(datasets))
    if hparams.get('overfit_test'):
        datasets = {
            key: SingleBatchWrapper(
                dataset,
                num_iterations=hparams.get('overfit_test_iterations', 1)) 
            for key, dataset in datasets.items()}
    
    print("DATASETS:")
    print(datasets["train"])
    print(datasets["valid"])
    '''
    # Initialize the Brain object to prepare for mask training.
    wavenet_brain = WavenetBrain(
        modules=hparams["modules"],
        opt_class=hparams["opt_class"],
        hparams=hparams,
        run_opts=run_opts,
        checkpointer=hparams["checkpointer"],
    )
  
    # The `fit()` method iterates the training loop, calling the methods
    # necessary to update the parameters of the model. Since all objects
    # with changing state are managed by the Checkpointer, training can be
    # stopped at any point, and will be resumed on next call.
    wavenet_brain.fit(
        epoch_counter=wavenet_brain.hparams.epoch_counter,
        train_set=datasets["train"],
        # TODO: Implement splitting - this is not ready yet
        valid_set=datasets["valid"],
        train_loader_kwargs=hparams["dataloader_options"],
        valid_loader_kwargs=hparams["dataloader_options"],
    )

if __name__ == '__main__':
    torch.cuda.empty_cache()
    main()