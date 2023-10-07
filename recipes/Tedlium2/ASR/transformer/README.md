# Tedlium2 ASR with Transformers
This folder contains the scripts to train a Transformer-based speech recognizer.

You can download Tedlium2 at https://lium.univ-lemans.fr/ted-lium2/

# How to Run:

1. Begin by training the tokenizer:

```shell
cd ../../Tokenizer
python train.py hyperparams/tedlium2_500_bpe.yaml
```

Please, read  ../../Tokenizer/README.md before proceeding.
This training script will handle data preparation and tokenizer training. Note that this script prepares the data in a format suitable for training the ASR model. 
Specifically, it segments the entire TED recording into individual utterance-level recordings, resulting in approximately 46 gigabytes of data. 
The CSV files generated for training, development, and testing are also utilized in ASR training.
 
**IMPORTANT:** Rnsure you complete this step before proceeding to train the ASR Model.

2. Proceed to train the ASR model:

```shell
python train.py hparams/branchformer_large.yaml 
```

This script relies on the data manifest files prepared in step 1.


# Results

| Release | hyperparams file |  Test WER (No LM) | HuggingFace link | Model link | GPUs |
|:-------------:|:-------------:|:-------------:|:---------------------------:| :-----:| :-----:|
| 23-05-23 | branchformer_large.yaml | 7.9 | Not Avail. | Not Avail. | 4xA100 80GB |


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
