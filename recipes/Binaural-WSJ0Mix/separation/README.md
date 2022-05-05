# Speech separation with Binaural-WSJ0Mix
This folder contains some recipes for the Binaural-WSJ0Mix task (2/3 sources). Please refer to [Real-time binaural speech separation with preserved spatial cues](https://ieeexplore.ieee.org/abstract/document/9053215) [1] for details.


Additional dependency:
```
pip install mir_eval, pyroomacoustics
```

To run it:

```
python train-parallel.py hparams/convtasnet-parallel.yaml --data_folder yourpath/binaural-wsj0mix/2speakers
```
Note that during training we print the negative SNR instead of SI-SNR because the scale-invariance property of SI-SNR makes it insensitive to power rescaling of the estimated signal, which may fail in preserving the ILD between the outputs.


# Binaural WSJ0-2mix and WSJ0-3mix dataset creation
* The best way to create the datasets is using the the scripts at https://github.com/huangzj421/Binaural-WSJ0Mix.
* The dataset creation script assumes that the original WSJ0 and CIPIC HRTF files are already converted to .wav .


# Dynamic Mixing:

* This recipe supports dynamic mixing where the training data is dynamically created in order to obtain new utterance combinations during training. For this you need to have the WSJ0 dataset (available though LDC at `https://catalog.ldc.upenn.edu/LDC93S6A`).


# Results

Here are the SNRi results (in dB) as well as ITD and ILD errors as the metric for the accuracy of preserving interaural cues:

| | SNRi | delta-ITD | delta-ILD |
| --- | --- | --- | --- |
|ConvTasnet-binaural| 12.39 | 6.17 | 0.32 |
|ConvTasnet-cross| 11.9 | 5.69 | 0.37 |
|ConvTasnet-parallel| 11.69 | 3.23 | 0.23 |

* ConvTasnet-binaural refers to ConvTasnet applied to each channel independently.
* ConvTasnet-cross refers to cross-channel features concatenated to the encoder output.
* ConvTasnet-parallel refers to the proposed multiinput-multi-output (MIMO) TasNet in the original paper [1].


# Example calls for running the training scripts


* Binaural-WSJ0-2Mix training without dynamic mixing `python train-parallel.py hparams/convtasnet-parallel.yaml --data_folder yourpath/binaural-wsj0mix/2speakers`

* WSJ0-2Mix training with dynamic mixing `python train-parallel.py hparams/convtasnet-parallel.yaml --data_folder yourpath/binaural-wsj0mix/2speakers --base_folder_dm yourpath/wsj0/si_tr_s --hrtf_wav_path yourpath/hrtf/wav_database --dynamic_mixing True`


# Multi-GPU training

You can run the following command to train the model using Distributed Data Parallel (DDP) with 2 GPUs:

```
 python -m torch.distributed.launch --nproc_per_node=2 train-parallel.py hparams/convtasnet-parallel.yaml --data_folder /yourdatapath --distributed_launch --distributed_backend='nccl'
```
You can add the other runtime options as appropriate. For more complete information on multi-GPU usage, take a look at this [tutorial](https://colab.research.google.com/drive/13pBUacPiotw1IvyffvGZ-HrtBr9T6l15?usp=sharing).




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

# References

> [1] Han, Cong, Yi Luo, and Nima Mesgarani. "Real-time binaural speech separation with preserved spatial cues." ICASSP 2020-2020 IEEE International Conference on Acoustics, Speech and Signal Processing (ICASSP). IEEE, 2020.