"""
Neural network modules for the THiFi-GAN: Generative Adversarial Networks for
Efficient and High Fidelity Speech Synthesis

Authors
* Duret Jarod 2021
"""

# Adapted from https://github.com/jik876/hifi-gan/ and https://github.com/coqui-ai/TTS/
# MIT License

# Copyright (c) 2020 Jungil Kong

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import torch
import torch.nn.functional as F
import torch.nn as nn
from torch.nn import Conv1d, ConvTranspose1d, AvgPool1d, Conv2d
from torch.nn.utils import weight_norm, remove_weight_norm, spectral_norm
from torchaudio import transforms

LRELU_SLOPE = 0.1


def get_padding(kernel_size, dilation=1):
    return int((kernel_size * dilation - dilation) / 2)

def dynamic_range_compression(x, C=1, clip_val=1e-5):
    return torch.log(torch.clamp(x, min=clip_val) * C)

def mel_spectogram(mel_params, audio):
    audio_to_mel = transforms.MelSpectrogram(
        sample_rate=mel_params["sample_rate"],
        hop_length=mel_params["hop_length"],
        win_length=mel_params["win_length"],
        n_fft=mel_params["n_fft"],
        n_mels=mel_params["n_mel_channels"],
        f_min=mel_params["mel_fmin"],
        f_max=mel_params["mel_fmax"],
        power=mel_params["power"],
        normalized=mel_params["mel_normalized"],
    ).to(audio.device)

    mel = audio_to_mel(audio)

    if mel_params["dynamic_range_compression"]:
        mel = dynamic_range_compression(mel)

    return mel

##################################
# Generator 
##################################

class ResBlock1(torch.nn.Module):
    """
    Residual Block Type 1. It has 3 convolutional layers in each convolutiona block.
    
    Args:
        channels (int): number of hidden channels for the convolutional layers.
        kernel_size (int): size of the convolution filter in each layer.
        dilations (list): list of dilation value for each conv layer in a block.
    """
    def __init__(self, channels, kernel_size=3, dilation=(1, 3, 5)):
        super().__init__()
        self.convs1 = nn.ModuleList(
            [
                weight_norm(
                    Conv1d(
                        channels,
                        channels,
                        kernel_size,
                        1,
                        dilation=dilation[0],
                        padding=get_padding(kernel_size, dilation[0]),
                    )
                ),
                weight_norm(
                    Conv1d(
                        channels,
                        channels,
                        kernel_size,
                        1,
                        dilation=dilation[1],
                        padding=get_padding(kernel_size, dilation[1]),
                    )
                ),
                weight_norm(
                    Conv1d(
                        channels,
                        channels,
                        kernel_size,
                        1,
                        dilation=dilation[2],
                        padding=get_padding(kernel_size, dilation[2]),
                    )
                ),
            ]
        )

        self.convs2 = nn.ModuleList(
            [
                weight_norm(
                    Conv1d(channels, channels, kernel_size, 1, dilation=1, padding=get_padding(kernel_size, 1))
                ),
                weight_norm(
                    Conv1d(channels, channels, kernel_size, 1, dilation=1, padding=get_padding(kernel_size, 1))
                ),
                weight_norm(
                    Conv1d(channels, channels, kernel_size, 1, dilation=1, padding=get_padding(kernel_size, 1))
                ),
            ]
        )

    def forward(self, x):
        """
        Args:
            x (Tensor): input tensor.
        Returns:
            Tensor: output tensor.
        Shapes:
            x: [B, C, T]
        """
        for c1, c2 in zip(self.convs1, self.convs2):
            xt = F.leaky_relu(x, LRELU_SLOPE)
            xt = c1(xt)
            xt = F.leaky_relu(xt, LRELU_SLOPE)
            xt = c2(xt)
            x = xt + x
        return x

    def remove_weight_norm(self):
        for l in self.convs1:
            remove_weight_norm(l)
        for l in self.convs2:
            remove_weight_norm(l)


class ResBlock2(torch.nn.Module):
    """Residual Block Type 1. It has 3 convolutional layers in each convolutiona block.

    Args:
        channels (int): number of hidden channels for the convolutional layers.
        kernel_size (int): size of the convolution filter in each layer.
        dilations (list): list of dilation value for each conv layer in a block.
    """
    def __init__(self, channels, kernel_size=3, dilation=(1, 3)):
        super().__init__()
        self.convs = nn.ModuleList(
            [
                weight_norm(
                    Conv1d(
                        channels,
                        channels,
                        kernel_size,
                        1,
                        dilation=dilation[0],
                        padding=get_padding(kernel_size, dilation[0]),
                    )
                ),
                weight_norm(
                    Conv1d(
                        channels,
                        channels,
                        kernel_size,
                        1,
                        dilation=dilation[1],
                        padding=get_padding(kernel_size, dilation[1]),
                    )
                ),
            ]
        )

    def forward(self, x):
        for c in self.convs:
            xt = F.leaky_relu(x, LRELU_SLOPE)
            xt = c(xt)
            x = xt + x
        return x

    def remove_weight_norm(self):
        for l in self.convs:
            remove_weight_norm(l)


class HifiganGenerator(torch.nn.Module):
    """HiFiGAN Generator with Multi-Receptive Field Fusion (MRF)

        Args:
            in_channels (int): number of input tensor channels.
            out_channels (int): number of output tensor channels.
            resblock_type (str): type of the `ResBlock`. '1' or '2'.
            resblock_dilation_sizes (List[List[int]]): list of dilation values in each layer of a `ResBlock`.
            resblock_kernel_sizes (List[int]): list of kernel sizes for each `ResBlock`.
            upsample_kernel_sizes (List[int]): list of kernel sizes for each transposed convolution.
            upsample_initial_channel (int): number of channels for the first upsampling layer. This is divided by 2
                for each consecutive upsampling layer.
            upsample_factors (List[int]): upsampling factors (stride) for each upsampling layer.
            inference_padding (int): constant padding applied to the input at inference time. Defaults to 5.
    """
    def __init__(
        self,
        in_channels,
        out_channels,
        resblock_type,
        resblock_dilation_sizes,
        resblock_kernel_sizes,
        upsample_kernel_sizes,
        upsample_initial_channel,
        upsample_factors,
        inference_padding=5,
        cond_channels=0,
        conv_pre_weight_norm=True,
        conv_post_weight_norm=True,
        conv_post_bias=True,
    ):
        super().__init__()
        self.inference_padding = inference_padding
        self.num_kernels = len(resblock_kernel_sizes)
        self.num_upsamples = len(upsample_factors)
        # initial upsampling layers
        self.conv_pre = weight_norm(Conv1d(in_channels, upsample_initial_channel, 7, 1, padding=3))
        resblock = ResBlock1 if resblock_type == "1" else ResBlock2
        # upsampling layers
        self.ups = nn.ModuleList()
        for i, (u, k) in enumerate(zip(upsample_factors, upsample_kernel_sizes)):
            self.ups.append(
                weight_norm(
                    ConvTranspose1d(
                        upsample_initial_channel // (2 ** i),
                        upsample_initial_channel // (2 ** (i + 1)),
                        k,
                        u,
                        padding=(k - u) // 2,
                    )
                )
            )
        # MRF blocks
        self.resblocks = nn.ModuleList()
        for i in range(len(self.ups)):
            ch = upsample_initial_channel // (2 ** (i + 1))
            for _, (k, d) in enumerate(zip(resblock_kernel_sizes, resblock_dilation_sizes)):
                self.resblocks.append(resblock(ch, k, d))
        # post convolution layer
        self.conv_post = weight_norm(Conv1d(ch, out_channels, 7, 1, padding=3, bias=conv_post_bias))
        if cond_channels > 0:
            self.cond_layer = nn.Conv1d(cond_channels, upsample_initial_channel, 1)

        if not conv_pre_weight_norm:
            remove_weight_norm(self.conv_pre)

        if not conv_post_weight_norm:
            remove_weight_norm(self.conv_post)

    def forward(self, x, g=None):
        """
        Args:
            x (Tensor): feature input tensor.
            g (Tensor): global conditioning input tensor.
        Returns:
            Tensor: output waveform.
        Shapes:
            x: [B, C, T]
            Tensor: [B, 1, T]
        """
        o = self.conv_pre(x)
        if hasattr(self, "cond_layer"):
            o = o + self.cond_layer(g)
        for i in range(self.num_upsamples):
            o = F.leaky_relu(o, LRELU_SLOPE)
            o = self.ups[i](o)
            z_sum = None
            for j in range(self.num_kernels):
                if z_sum is None:
                    z_sum = self.resblocks[i * self.num_kernels + j](o)
                else:
                    z_sum += self.resblocks[i * self.num_kernels + j](o)
            o = z_sum / self.num_kernels
        o = F.leaky_relu(o)
        o = self.conv_post(o)
        o = torch.tanh(o)
        return o

    @torch.no_grad()
    def inference(self, c):
        """
        Args:
            x (Tensor): conditioning input tensor.
        Returns:
            Tensor: output waveform.
        Shapes:
            x: [B, C, T]
            Tensor: [B, 1, T]
        """
        c = c.to(self.conv_pre.weight.device)
        c = torch.nn.functional.pad(c, (self.inference_padding, self.inference_padding), "replicate")
        return self.forward(c)

    def remove_weight_norm(self):
        print("Removing weight norm...")
        for l in self.ups:
            remove_weight_norm(l)
        for l in self.resblocks:
            l.remove_weight_norm()
        remove_weight_norm(self.conv_pre)
        remove_weight_norm(self.conv_post)


##################################
# DISCRIMINATOR 
##################################

class DiscriminatorP(torch.nn.Module):
    """HiFiGAN Periodic Discriminator
    Takes every Pth value from the input waveform and applied a stack of convoluations.
    Note:
        if `period` is 2
        `waveform = [1, 2, 3, 4, 5, 6 ...] --> [1, 3, 5 ... ] --> convs -> score, feat`
    Args:
        x (Tensor): input waveform.
    Returns:
        [Tensor]: discriminator scores per sample in the batch.
        [List[Tensor]]: list of features from each convolutional layer.
    Shapes:
        x: [B, 1, T]
    """

    def __init__(self, period, kernel_size=5, stride=3, use_spectral_norm=False):
        super().__init__()
        self.period = period
        get_padding = lambda k, d: int((k * d - d) / 2)
        norm_f = nn.utils.spectral_norm if use_spectral_norm else nn.utils.weight_norm
        self.convs = nn.ModuleList(
            [
                norm_f(nn.Conv2d(1, 32, (kernel_size, 1), (stride, 1), padding=(get_padding(kernel_size, 1), 0))),
                norm_f(nn.Conv2d(32, 128, (kernel_size, 1), (stride, 1), padding=(get_padding(kernel_size, 1), 0))),
                norm_f(nn.Conv2d(128, 512, (kernel_size, 1), (stride, 1), padding=(get_padding(kernel_size, 1), 0))),
                norm_f(nn.Conv2d(512, 1024, (kernel_size, 1), (stride, 1), padding=(get_padding(kernel_size, 1), 0))),
                norm_f(nn.Conv2d(1024, 1024, (kernel_size, 1), 1, padding=(2, 0))),
            ]
        )
        self.conv_post = norm_f(nn.Conv2d(1024, 1, (3, 1), 1, padding=(1, 0)))

    def forward(self, x):
        """
        Args:
            x (Tensor): input waveform.
        Returns:
            [Tensor]: discriminator scores per sample in the batch.
            [List[Tensor]]: list of features from each convolutional layer.
        Shapes:
            x: [B, 1, T]
        """
        feat = []

        # 1d to 2d
        b, c, t = x.shape
        if t % self.period != 0:  # pad first
            n_pad = self.period - (t % self.period)
            x = F.pad(x, (0, n_pad), "reflect")
            t = t + n_pad
        x = x.view(b, c, t // self.period, self.period)

        for l in self.convs:
            x = l(x)
            x = F.leaky_relu(x, LRELU_SLOPE)
            feat.append(x)
        x = self.conv_post(x)
        feat.append(x)
        x = torch.flatten(x, 1, -1)

        return x, feat


class MultiPeriodDiscriminator(torch.nn.Module):
    """HiFiGAN Multi-Period Discriminator (MPD)
    Wrapper for the `PeriodDiscriminator` to apply it in different periods.
    Periods are suggested to be prime numbers to reduce the overlap between each discriminator.
    """

    def __init__(self, use_spectral_norm=False):
        super().__init__()
        self.discriminators = nn.ModuleList(
            [
                DiscriminatorP(2, use_spectral_norm=use_spectral_norm),
                DiscriminatorP(3, use_spectral_norm=use_spectral_norm),
                DiscriminatorP(5, use_spectral_norm=use_spectral_norm),
                DiscriminatorP(7, use_spectral_norm=use_spectral_norm),
                DiscriminatorP(11, use_spectral_norm=use_spectral_norm),
            ]
        )

    def forward(self, x):
        """
        Args:
            x (Tensor): input waveform.
        Returns:
        [List[Tensor]]: list of scores from each discriminator.
            [List[List[Tensor]]]: list of list of features from each discriminator's each convolutional layer.
        Shapes:
            x: [B, 1, T]
        """
        scores = []
        feats = []
        for _, d in enumerate(self.discriminators):
            score, feat = d(x)
            scores.append(score)
            feats.append(feat)
        return scores, feats


class DiscriminatorS(torch.nn.Module):
    """HiFiGAN Scale Discriminator.
    It is similar to `MelganDiscriminator` but with a specific architecture explained in the paper.
    Args:
        use_spectral_norm (bool): if `True` swith to spectral norm instead of weight norm.
    """

    def __init__(self, use_spectral_norm=False):
        super().__init__()
        norm_f = nn.utils.spectral_norm if use_spectral_norm else nn.utils.weight_norm
        self.convs = nn.ModuleList(
            [
                norm_f(nn.Conv1d(1, 128, 15, 1, padding=7)),
                norm_f(nn.Conv1d(128, 128, 41, 2, groups=4, padding=20)),
                norm_f(nn.Conv1d(128, 256, 41, 2, groups=16, padding=20)),
                norm_f(nn.Conv1d(256, 512, 41, 4, groups=16, padding=20)),
                norm_f(nn.Conv1d(512, 1024, 41, 4, groups=16, padding=20)),
                norm_f(nn.Conv1d(1024, 1024, 41, 1, groups=16, padding=20)),
                norm_f(nn.Conv1d(1024, 1024, 5, 1, padding=2)),
            ]
        )
        self.conv_post = norm_f(nn.Conv1d(1024, 1, 3, 1, padding=1))

    def forward(self, x):
        """
        Args:
            x (Tensor): input waveform.
        Returns:
            Tensor: discriminator scores.
            List[Tensor]: list of features from the convolutiona layers.
        """
        feat = []
        for l in self.convs:
            x = l(x)
            x = F.leaky_relu(x, LRELU_SLOPE)
            feat.append(x)
        x = self.conv_post(x)
        feat.append(x)
        x = torch.flatten(x, 1, -1)
        return x, feat


class MultiScaleDiscriminator(torch.nn.Module):
    """HiFiGAN Multi-Scale Discriminator.
    It is similar to `MultiScaleMelganDiscriminator` but specially tailored for HiFiGAN as in the paper.
    """

    def __init__(self):
        super().__init__()
        self.discriminators = nn.ModuleList(
            [
                DiscriminatorS(use_spectral_norm=True),
                DiscriminatorS(),
                DiscriminatorS(),
            ]
        )
        self.meanpools = nn.ModuleList([nn.AvgPool1d(4, 2, padding=2), nn.AvgPool1d(4, 2, padding=2)])

    def forward(self, x):
        """
        Args:
            x (Tensor): input waveform.
        Returns:
            List[Tensor]: discriminator scores.
            List[List[Tensor]]: list of list of features from each layers of each discriminator.
        """
        scores = []
        feats = []
        for i, d in enumerate(self.discriminators):
            if i != 0:
                x = self.meanpools[i - 1](x)
            score, feat = d(x)
            scores.append(score)
            feats.append(feat)
        return scores, feats


class HifiganDiscriminator(nn.Module):
    """HiFiGAN discriminator wrapping MPD and MSD."""

    def __init__(self):
        super().__init__()
        self.mpd = MultiPeriodDiscriminator()
        self.msd = MultiScaleDiscriminator()

    def forward(self, x):
        """
        Args:
            x (Tensor): input waveform.
        Returns:
            List[Tensor]: discriminator scores.
            List[List[Tensor]]: list of list of features from each layers of each discriminator.
        """
        scores, feats = self.mpd(x)
        scores_, feats_ = self.msd(x)
        return scores + scores_, feats + feats_


#################################
# GENERATOR LOSSES
#################################


def stft(x, n_fft, hop_length, win_length, window_fn="hann_window"):
    o = torch.stft(
            x.squeeze(1),
            n_fft,
            hop_length,
            win_length,
            #nn.Parameter(getattr(torch, window_fn)(win_length), requires_grad=False),
    )
    M = o[:, :, :, 0]
    P = o[:, :, :, 1]
    S = torch.sqrt(torch.clamp(M ** 2 + P ** 2, min=1e-8))
    return S


class STFTLoss(nn.Module):
    """STFT loss. Input generate and real waveforms are converted
    to spectrograms compared with L1 and Spectral convergence losses.
    It is from ParallelWaveGAN paper https://arxiv.org/pdf/1910.11480.pdf"""

    def __init__(self, n_fft, hop_length, win_length):
        super().__init__()
        self.n_fft = n_fft
        self.hop_length = hop_length
        self.win_length = win_length

    def forward(self, y_hat, y):
        y_hat_M = stft(y_hat, self.n_fft, self.hop_length, self.win_length)
        y_M = stft(y, self.n_fft, self.hop_length, self.win_length)
        # magnitude loss
        loss_mag = F.l1_loss(torch.log(y_M), torch.log(y_hat_M))
        # spectral convergence loss
        loss_sc = torch.norm(y_M - y_hat_M, p="fro") / torch.norm(y_M, p="fro")
        return loss_mag, loss_sc


class MultiScaleSTFTLoss(torch.nn.Module):
    """Multi-scale STFT loss. Input generate and real waveforms are converted
    to spectrograms compared with L1 and Spectral convergence losses.
    It is from ParallelWaveGAN paper https://arxiv.org/pdf/1910.11480.pdf"""

    def __init__(self, n_ffts=(1024, 2048, 512), hop_lengths=(120, 240, 50), win_lengths=(600, 1200, 240)):
        super().__init__()
        self.loss_funcs = torch.nn.ModuleList()
        for n_fft, hop_length, win_length in zip(n_ffts, hop_lengths, win_lengths):
            self.loss_funcs.append(STFTLoss(n_fft, hop_length, win_length))

    def forward(self, y_hat, y):
        N = len(self.loss_funcs)
        loss_sc = 0
        loss_mag = 0
        for f in self.loss_funcs:
            lm, lsc = f(y_hat, y)
            loss_mag += lm
            loss_sc += lsc
        loss_sc /= N
        loss_mag /= N
        return loss_mag, loss_sc


class L1SpecLoss(nn.Module):
    """L1 Loss over Spectrograms as described in HiFiGAN paper https://arxiv.org/pdf/2010.05646.pdf"""

    def __init__(self,
        sample_rate=22050,
        hop_length=256,
        win_length=24,
        n_mel_channels=80,
        n_fft=1024,
        n_stft=1024 // 2 + 1,
        mel_fmin=0.0,
        mel_fmax=8000.0,
        mel_normalized=False,
        power=1.5,
        dynamic_range_compression=True
        ):
        super().__init__()

        self.mel_params = {
            "sample_rate": sample_rate,
            "hop_length": hop_length,
            "win_length": win_length,
            "n_mel_channels": n_mel_channels,
            "n_fft": n_fft,
            "n_stft": n_stft,
            "mel_fmin": mel_fmin,
            "mel_fmax": mel_fmax,
            "mel_normalized": mel_normalized,
            "power": power,
            "dynamic_range_compression": dynamic_range_compression, 
        }

    def forward(self, y_hat, y):

        y_hat_M = mel_spectogram(self.mel_params, y_hat)
        y_M = mel_spectogram(self.mel_params, y)
        
        # magnitude loss
        #loss_mag = F.l1_loss(torch.log(y_M), torch.log(y_hat_M))
        loss_mag = F.l1_loss(y_M, y_hat_M)
        return loss_mag


class MSEGLoss(nn.Module):
    """Mean Squared Generator Loss"""

    def forward(self, score_real):
        loss_fake = F.mse_loss(score_real, score_real.new_ones(score_real.shape))
        return loss_fake


class MelganFeatureLoss(nn.Module):
    def __init__(
        self,
    ):
        super().__init__()
        self.loss_func = nn.L1Loss()

    # pylint: disable=no-self-use
    def forward(self, fake_feats, real_feats):
        loss_feats = 0
        num_feats = 0
        for idx, _ in enumerate(fake_feats):
            for fake_feat, real_feat in zip(fake_feats[idx], real_feats[idx]):
                loss_feats += self.loss_func(fake_feat, real_feat)
                num_feats += 1
        loss_feats = loss_feats / num_feats
        return loss_feats


##################################
# DISCRIMINATOR LOSSES
##################################


class MSEDLoss(nn.Module):
    """Mean Squared Discriminator Loss"""

    def __init__(
        self,
    ):
        super().__init__()
        self.loss_func = nn.MSELoss()

    def forward(self, score_fake, score_real):
        loss_real = self.loss_func(score_real, score_real.new_ones(score_real.shape))
        loss_fake = self.loss_func(score_fake, score_fake.new_zeros(score_fake.shape))
        loss_d = loss_real + loss_fake
        return loss_d, loss_real, loss_fake


#####################################
# LOSS WRAPPERS
#####################################


def _apply_G_adv_loss(scores_fake, loss_func):
    """Compute G adversarial loss function
    and normalize values"""
    adv_loss = 0
    if isinstance(scores_fake, list):
        for score_fake in scores_fake:
            fake_loss = loss_func(score_fake)
            adv_loss += fake_loss
        adv_loss /= len(scores_fake)
    else:
        fake_loss = loss_func(scores_fake)
        adv_loss = fake_loss
    return adv_loss


def _apply_D_loss(scores_fake, scores_real, loss_func):
    """Compute D loss func and normalize loss values"""
    loss = 0
    real_loss = 0
    fake_loss = 0
    if isinstance(scores_fake, list):
        # multi-scale loss
        for score_fake, score_real in zip(scores_fake, scores_real):
            total_loss, real_loss, fake_loss = loss_func(score_fake=score_fake, score_real=score_real)
            loss += total_loss
            real_loss += real_loss
            fake_loss += fake_loss
        # normalize loss values with number of scales (discriminators)
        loss /= len(scores_fake)
        real_loss /= len(scores_real)
        fake_loss /= len(scores_fake)
    else:
        # single scale loss
        total_loss, real_loss, fake_loss = loss_func(scores_fake, scores_real)
        loss = total_loss
    return loss, real_loss, fake_loss



##################################
# MODEL LOSSES
##################################

class GeneratorLoss(nn.Module):

    def __init__(
        self,
        stft_loss = None,
        stft_loss_weight = 0,
        mseg_loss = None,
        mseg_loss_weight = 0,
        feat_match_loss = None,
        feat_match_loss_weight = 0,
        l1_spec_loss = None,
        l1_spec_loss_weight = 0
        ):
        super().__init__()
        self.stft_loss = stft_loss
        self.stft_loss_weight = stft_loss_weight
        self.mseg_loss = mseg_loss
        self.mseg_loss_weight = mseg_loss_weight
        self.feat_match_loss = feat_match_loss
        self.feat_match_loss_weight = feat_match_loss_weight
        self.l1_spec_loss = l1_spec_loss
        self.l1_spec_loss_weight = l1_spec_loss_weight

    def forward(
        self,
        y_hat=None,
        y=None,
        scores_fake=None,
        feats_fake=None,
        feats_real=None,
        ):
        gen_loss = 0
        adv_loss = 0
        loss = {}

        #STFT Loss
        if self.stft_loss:
            stft_loss_mg, stft_loss_sc = self.stft_loss(y_hat[:, :, : y.size(2)].squeeze(1), y.squeeze(1))
            loss["G_stft_loss_mg"] = stft_loss_mg
            loss["G_stft_loss_sc"] = stft_loss_sc
            gen_loss = gen_loss + self.stft_loss_weight * (stft_loss_mg + stft_loss_sc)

        # L1 Spec loss
        if self.l1_spec_loss:
            l1_spec_loss = self.l1_spec_loss(y_hat, y)
            loss["G_l1_spec_loss"] = l1_spec_loss
            gen_loss = gen_loss + self.l1_spec_loss_weight * l1_spec_loss

        # multiscale MSE adversarial loss
        if self.mseg_loss and scores_fake is not None:
            mse_fake_loss = _apply_G_adv_loss(scores_fake, self.mseg_loss)
            loss["G_mse_fake_loss"] = mse_fake_loss
            adv_loss = adv_loss + self.mseg_loss_weight * mse_fake_loss

        # Feature Matching Loss
        if self.feat_match_loss and not feats_fake is None:
            feat_match_loss = self.feat_match_loss(feats_fake, feats_real)
            loss["G_feat_match_loss"] = feat_match_loss
            adv_loss = adv_loss + self.feat_match_loss_weight * feat_match_loss
        loss["G_loss"] = gen_loss + adv_loss
        loss["G_gen_loss"] = gen_loss
        loss["G_adv_loss"] = adv_loss

        return loss



class DiscriminatorLoss(nn.Module):

    def __init__(self, msed_loss=None):
        super().__init__()
        self.msed_loss = msed_loss

    def forward(self, scores_fake, scores_real):
        disc_loss = 0
        loss = {}

        if self.msed_loss:
            mse_D_loss, mse_D_real_loss, mse_D_fake_loss = _apply_D_loss(
                scores_fake=scores_fake, scores_real=scores_real, loss_func=self.msed_loss
            )
            loss["D_mse_gan_loss"] = mse_D_loss
            loss["D_mse_gan_real_loss"] = mse_D_real_loss
            loss["D_mse_gan_fake_loss"] = mse_D_fake_loss
            disc_loss += mse_D_loss

        loss["D_loss"] = disc_loss
        return loss