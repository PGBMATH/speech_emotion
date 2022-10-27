#!/usr/bin/env/python3
"""This is a test script for creating a list of expected outcomes (before refactoring);
then, manual editing might change YAMLs and/or code; another test runs to compare results
(after refactoring to before). The target is a list of known HF repos.

The goal is to identify to which extent changes break existing functionality.
Then, larger changes to code base can be rolled out more assured.

Tested with dependencies:
pip install huggingface_hub==0.10.1 datasets==2.6.1 transformers==4.23.1

dependencies as of: Oct 11, 2022; Oct 14, 2022; Oct 11, 2022.

Authors
 * Andreas Nautsch, 2022
"""

import os
import sys
import tqdm
import yaml
import torch  # noqa
import importlib  # noqa
import subprocess
import speechbrain  # noqa
from glob import glob
from copy import deepcopy
from torch.utils.data import DataLoader
from hyperpyyaml import load_hyperpyyaml

# from speechbrain.utils.distributed import run_on_main
from speechbrain.utils.train_logger import FileTrainLogger
from speechbrain.pretrained.interfaces import foreign_class  # noqa
from speechbrain.dataio.dataloader import LoopedLoader, make_dataloader


def init(new_interfaces_git, new_interfaces_branch, new_interfaces_local_dir):
    # set up git etc
    if not os.path.exists(new_interfaces_local_dir):
        # note: not checking for anything
        cmd_out_clone = subprocess.run(
            ["git", "clone", new_interfaces_git, new_interfaces_local_dir],
            capture_output=True,
        )
        print(f"\tgit clone log: {cmd_out_clone}")
        cwd = os.getcwd()
        os.chdir(new_interfaces_local_dir)
        cmd_out_co = subprocess.run(
            ["git", "checkout", new_interfaces_branch], capture_output=True
        )
        print(f"\tgit checkout log: {cmd_out_co}")
        os.chdir(cwd)

    updates_dir = f"{new_interfaces_local_dir}/updates_pretrained_models"
    return updates_dir


def get_model(repo, values, updates_dir=None):
    # get the pretrained class; model & predictions
    kwargs = {
        "source": f"speechbrain/{repo}",
        "savedir": f"pretrained_models/{repo}",
    }

    if updates_dir is not None:
        kwargs["hparams_file"] = f"{updates_dir}/{repo}/hyperparams.yaml"

    if values["foreign"] is None:
        obj = eval(f'"speechbrain.pretrained".{values["cls"]}')
        model = obj.from_hparams(**kwargs)
    else:
        kwargs["pymodule_file"] = values["foreign"]
        kwargs["classname"] = values["cls"]
        model = foreign_class(**kwargs)

    model.modules.eval()

    return model


def get_prediction(repo, values, updates_dir=None):
    # updates_dir controls whether/not we are in the refactored results (None: expected results; before refactoring)

    def sanitize(data):
        # yaml outputs in clean
        if isinstance(data, torch.Tensor):
            data = data.detach().cpu().numpy()
        return data

    model = get_model(repo, values, updates_dir)  # noqa

    try:
        if values["prediction"] is None:
            # simulate batch from single file
            prediction = eval(
                f'model.{values["fnx"]}(model.load_audio("{repo}/{values["sample"]}", savedir="pretrained_models/{repo}").unsqueeze(0), torch.tensor([1.0]))'
            )
        else:
            # load audio from remote to local repo folder
            eval(
                f'model.load_audio("{repo}/{values["sample"]}", savedir="pretrained_models/{repo}")'
            )
            # run a single file interface call
            prediction = eval(values["prediction"])

    except Exception:
        # use an example audio if no audio can be loaded
        print(f'\tWARNING - no audio found on HF: {repo}/{values["sample"]}')
        prediction = eval(
            f'model.{values["fnx"]}(model.load_audio("tests/samples/single-mic/example1.wav", savedir="pretrained_models/{repo}").unsqueeze(0), torch.tensor([1.0]))'
        )

    finally:
        del model

    return [sanitize(x[0]) for x in prediction]


def gather_expected_results(
    new_interfaces_git="https://github.com/speechbrain/speechbrain",
    new_interfaces_branch="testing-refactoring",
    new_interfaces_local_dir="tests/tmp/hf_interfaces",
    yaml_path="tests/tmp/refactoring_results.yaml",
):
    """Before refactoring HF YAMLs and/or code (regarding wav2vec2), gather prediction results.

    Parameters
    ----------
    yaml_path : str
        Path where to store/load refactoring testing results for later comparison.

    """
    # load results, if existing -or- new from scratch
    if os.path.exists(yaml_path):
        with open(yaml_path) as yaml_in:
            results = yaml.safe_load(yaml_in)
    else:
        results = {}

    # go through each repo
    updates_dir = init(
        new_interfaces_git, new_interfaces_branch, new_interfaces_local_dir
    )
    repos = map(os.path.basename, glob(f"{updates_dir}/*"))
    for repo in repos:
        # skip if results are there
        if repo not in results.keys():
            # get values
            with open(f"{updates_dir}/{repo}/test.yaml") as yaml_test:
                values = load_hyperpyyaml(yaml_test)

            print(f"Collecting results for: {repo} w/ values={values}")
            prediction = get_prediction(repo, values)

            # extend the results
            results[repo] = {"before": prediction}
            with open(yaml_path, "w") as yaml_out:
                yaml.dump(results, yaml_out, default_flow_style=None)


def gather_refactoring_results(
    new_interfaces_git="https://github.com/speechbrain/speechbrain",
    new_interfaces_branch="testing-refactoring",
    new_interfaces_local_dir="tests/tmp/hf_interfaces",
    yaml_path="tests/tmp/refactoring_results.yaml",
):
    # expected results need to exist
    if os.path.exists(yaml_path):
        with open(yaml_path) as yaml_in:
            results = yaml.safe_load(yaml_in)

    # go through each repo
    updates_dir = init(
        new_interfaces_git, new_interfaces_branch, new_interfaces_local_dir
    )
    repos = map(os.path.basename, glob(f"{updates_dir}/*"))
    for repo in repos:
        # skip if results are there
        if repo not in results.keys():
            # get values
            with open(f"{updates_dir}/{repo}/test.yaml") as yaml_test:
                values = load_hyperpyyaml(yaml_test)

            print(
                f"Collecting refactoring results for: {repo} w/ values={values}"
            )

            # extend the results
            results[repo]["after"] = get_prediction(repo, values, updates_dir)
            results[repo]["same"] = (
                results[repo]["before"] == results[repo]["after"]
            )

            # update
            with open(yaml_path, "w") as yaml_out:
                yaml.dump(results, yaml_out, default_flow_style=None)

            print(f"\tsame: {results[repo]['same'] }")


def test_performance(repo, values, updates_dir=None, recipe_overrides={}):
    # Dataset depending file structure
    tmp_dir = f'tests/tmp/{values["dataset"]}'
    speechbrain.create_experiment_directory(experiment_directory=tmp_dir)
    stats_meta = {
        f'[{values["dataset"]}]\t{"BEFORE" if updates_dir is None else "AFTER"}': repo
    }

    # Load pretrained
    model = get_model(repo, values, updates_dir)  # noqa

    # Dataio preparation; we need the test sets only
    with open(values["recipe_yaml"]) as fin:
        recipe_hparams = load_hyperpyyaml(
            fin, values["overrides"] | recipe_overrides
        )

    # Dataset preparation is assumed to be done through recipes; before running this.
    eval(values["dataio"])
    test_datasets = deepcopy(eval(values["test_datasets"]))

    # harmonise
    if type(test_datasets) is not dict:
        tmp = {}
        if type(test_datasets) is list:
            for i, x in enumerate(test_datasets):
                tmp[i] = x
        else:
            tmp[0] = test_datasets
        test_datasets = tmp

    # prepare testing
    logger = FileTrainLogger(save_file=f"{tmp_dir}/{repo}.log")
    reporting = values["performance"]
    for metric, specs in values["performance"].items():
        reporting[metric]["handler"] = deepcopy(
            recipe_hparams[specs["handler"]]
        )()
    test_loader_kwargs = deepcopy(recipe_hparams[values["test_loader"]])
    del recipe_hparams

    for k in test_datasets.keys():  # keys are test_clean, test_other etc
        test_set = test_datasets[k]
        if not (
            isinstance(test_set, DataLoader)
            or isinstance(test_set, LoopedLoader)
        ):
            test_loader_kwargs["ckpt_prefix"] = None
            test_set = make_dataloader(test_set, **test_loader_kwargs)

        with torch.no_grad():
            for batch in tqdm(test_set, dynamic_ncols=True):
                batch = batch.to(model.device)
                wavs, wav_lens = batch.sig
                wavs, wav_lens = (  # noqa
                    wavs.to(model.device),
                    wav_lens.to(model.device),
                )
                predictions = eval(  # noqa
                    f'model.{values["fnx"]}(wavs, wav_lens)'
                )
                predicted = eval(values["predicted"])
                ids = batch.id
                targeted = [wrd.split(" ") for wrd in batch.wrd]
                for tracker in reporting.values():
                    tracker["handler"].append(ids, predicted, targeted)

        stats = {}
        for metric, tracker in reporting.items():
            stats[metric] = tracker["handler"].summarize(tracker["field"])
        logger.log_stats(
            stats_meta=stats_meta | {"Testing": k}, test_stats=stats,
        )
        return stats


# python tests/integration/HuggingFace_transformers/refactoring_checks.py tests/integration/HuggingFace_transformers/overrides.yaml --LibriSpeech_data="" --CommonVoice_EN_data="" --CommonVoice_FR_data="" --IEMOCAP_data=""
if __name__ == "__main__":
    hparams_file, run_opts, overrides = speechbrain.parse_arguments(
        sys.argv[1:]
    )
    # speechbrain.utils.distributed.ddp_init_group(run_opts)

    with open(hparams_file) as fin:
        dataset_overrides = load_hyperpyyaml(fin, overrides)

    # go through each repo
    updates_dir = init(
        dataset_overrides["new_interfaces_git"],
        dataset_overrides["new_interfaces_branch"],
        dataset_overrides["new_interfaces_local_dir"],
    )
    repos = map(
        os.path.basename,
        glob(f'{updates_dir}/{dataset_overrides["glob_filter"]}'),
    )
    for repo in repos:
        # get values
        with open(f"{updates_dir}/{repo}/test.yaml") as yaml_test:
            values = load_hyperpyyaml(yaml_test)

        # for this testing, some fields need to exist; skip otherwise
        if any(
            [
                entry not in values
                for entry in [
                    "dataset",
                    "overrides",
                    "dataio",
                    "test_datasets",
                    "test_loader",
                    "performance",
                    "predicted",
                ]
            ]
        ):
            continue

        # skip if datasets is not given
        if not dataset_overrides[f'{values["dataset"]}_data']:
            continue

        print(f"Run tests on: {repo} w/ values={values}")

        # Before refactoring
        stats_before = test_performance(
            repo,
            values,
            updates_dir=None,
            recipe_overrides=dataset_overrides[values["dataset"]],
        )

        # After refactoring
        stats_after = test_performance(
            repo,
            values,
            updates_dir=updates_dir,
            recipe_overrides=dataset_overrides[values["dataset"]],
        )
