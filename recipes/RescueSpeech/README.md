# RescueSpeech
RescuSpeech is a dataset specifically designed for performing noise robust speech recognition in the Search and Rescue domain. In this repository, we provide training recipes and pre-trained models that have been developed and evaluated using RescuSpeech data. These models aim to enhance the performance of speech recognizers in challenging and noisy environments.

This recipe supports training several models on the dataset
- **Task: ASR**- CRDNN, Wav2vec2, WavLM, Whisper
- **Task: Speech enhancement**-  SepFormer

# Training Strategies
We have explored multiple training strategies to improve noise robust speech recognition using RescuSpeech. The following methods have been implemented and evaluated:

1. A simple pipeline consisting solely of an ASR model,
    - Clean training
    - Multi-condition Training
2. Pipeline combining ASR and Speech Enhancement model
    - Model-combination I: Independent Training
    - Model-combination II: Joint Training


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


**Citing RescueSpeech**
- Dataset
```bibtex
{
}
```
- Paper
```bibtex
{
}
```