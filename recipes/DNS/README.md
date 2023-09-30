# **Speech Enhancement for Microsoft Deep Noise Suppression (DNS) Challenge – ICASSP 2022**
This repository contains training recipes for a speech enhancement system designed for the 4th Deep Noise Suppression Challenge, organized by Microsoft at Interspeech 2022. <br>
The Deep Noise Suppression Challenge features two distinct tracks:
1. **Real Time Non-Personalized DNS**
2. Real Time Personalized DNS (PDNS) for Fullband Audio

We focus on implementing solutions only for the first track, which involves real-time non-personalized DNS.

- **Model and Data** : For this challenge, we employ the [Sepformer model](https://arxiv.org/abs/2010.13154v2) to train our speech enhancement system. Our training utilizes 500 hours of fullband audio.

- **Evaluation Strategy** : We follow the official evaluation strategy outlined by the ITU-T P.835 subjective test framework. It measures speech quality, background noise quality, and overall audio quality. This is done using [DNSMOS P.835](https://arxiv.org/pdf/2110.01763.pdf), a machine learning-based model capable of predicting SIG (Speech Quality), BAK (Background Noise Quality), and OVRL (Overall Audio Quality).

**Related links**
- [Official Website](https://www.microsoft.com/en-us/research/academic-program/deep-noise-suppression-challenge-icassp-2022/)
- [DNS-4 ICASSP 2022 github repository](https://github.com/microsoft/DNS-Challenge/tree/5582dcf5ba43155621de72a035eb54a7d233af14)

## **DNS-4 dataset**
DNS-4 dataset once decompressed, the directory structure and sizes of datasets are:
```
datasets_fullband 892G
+-- dev_testset 1.7G
+-- impulse_responses 5.9G
+-- noise_fullband 58G
\-- clean_fullband 827G
    +-- emotional_speech 2.4G
    +-- french_speech 62G
    +-- german_speech 319G
    +-- italian_speech 42G
    +-- read_speech 299G
    +-- russian_speech 12G
    +-- spanish_speech 65G
    +-- vctk_wav48_silence_trimmed 27G
    \-- VocalSet_48kHz_mono 974M
```

### **Required disk space**
The `dns_download.py` download script downloads the Real-time DNS track data and de-compresses it. The compressed data takes around 550 GB of disk space and when de-compressed you would need 1 TB to store audio files.
However this is not the end, the downloaded clean-audio files, RIRs, and noisy-audio files are further used to synthesize clean-noisy audio pairs for training. This means further space will be needed to store the synthesized clean-noisy-noise audio.

**NOTE**
- This dataset download process can be extremely time-consuming. With a total of 126 splits (train, noise and dev data), the script downloads each split in a serial order. The script also allows concurrent data download (by enabling `--parallel_download` param) by using multiple threads (equal to number of your CPU cores). This is helpful especially when you have access to a large cluster. (Alternatively, you can download all 126 splits and decompress them at once by using array job submission.)

## **Getting started**
- STEP 1: Download DNS dataset.
- STEP 2: Synthesize noisy data.
- STEP 3: Begin training.

## Step 1: **Downloading Real-time DNS track dataset**
The DNS dataset can be downloaded by running the script below
```
python dns_download.py --compressed_path DNS-dataset --decompressed_path DNS-compressed
```
To use parallel downloading
```
python dns_download.py --compressed_path DNS-dataset --decompressed_path DNS-compressed --parallel_download
```

The compressed files are downloaded in `DNS-compressed` and further decompressed audio files can be found in `DNS-dataset`.

## Step 2: **Synthesize noisy data**
To synthesize clean-noisy audio for speech enhancement training (we add noise, RIR to clean fullband speech to synthesize clean-noisy pairs)
```
cd noisyspeech_synthesizer
python noisyspeech_synthesizer_singleprocess.py noisyspeech_synthesizer.yaml --uncompressed_path ../DNS-dataset/datasets_fullband/ --split_name <read_speech/german_speech/french_speech/italian_speech/russian_speech/spanish_speech>
```
Select one of `read_speech`, `german_speech`, `french_speech`, `italian_speech`, `russian_speech` or `spanish_speech`. <br>
*For more see `noisyspeech_synthesizer` on how to synthesize noisy files from clean audio and noise audio files.*


## Step 3: **Begin training**
To start training
```
cd enhancement
python train.py hparams/sepformer-dns-16k.yaml --data_folder <path/to/synthesized_data> --baseline_noisy_folder <path/to/baseline-noisy-testclips>
```
*For more details and how to perform evaluation, see `enhancement` folder on details about the main training script*

# **About SpeechBrain**
- Website: https://speechbrain.github.io/
- Code: https://github.com/speechbrain/speechbrain/
- HuggingFace: https://huggingface.co/speechbrain/


# **Citing SpeechBrain**
Please, cite SpeechBrain if you use it for your research or business.

```bibtex
@misc{speechbrain,
  title={{SpeechBrain}: A General-Purpose Speech Toolkit},
  author={Mirco Ravanelli and Titouan Parcollet and Peter Plantinga and Aku Rouhe and Samuele Cornell and Loren Lugosch and Cem Subakan and Nauman Dawalatabad and Abdelwahab Heba and Jianyuan Zhong and Ju-Chieh Chou and Sung-Lin Yeh and Szu-Wei Fu and Chien-Feng Liao and Elena Rastorgueva and François Grondin and William Aris and Hwidong Na and Yan Gao and Renato De Mori and Yoshua Bengio},
  year={2021},
  eprint={2106.04624},
  archivePrefix={arXiv},
  primaryClass={eess.AS},
  note={arXiv:2106.04624}
}
```


**Citing SepFormer**
```bibtex
@inproceedings{subakan2021attention,
      title={Attention is All You Need in Speech Separation},
      author={Cem Subakan and Mirco Ravanelli and Samuele Cornell and Mirko Bronzi and Jianyuan Zhong},
      year={2021},
      booktitle={ICASSP 2021}
}
```

**Citing DNS-4 dataset (ICASSP 2022)**
```bibtex
@inproceedings{dubey2022icassp,
  title={ICASSP 2022 Deep Noise Suppression Challenge},
  author={Dubey, Harishchandra and Gopal, Vishak and Cutler, Ross and Matusevych, Sergiy and Braun, Sebastian and Eskimez, Emre Sefik and Thakker, Manthan and Yoshioka, Takuya and Gamper, Hannes and Aichner, Robert},
  booktitle={ICASSP},
  year={2022}
}
```