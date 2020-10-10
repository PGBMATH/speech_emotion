"""Core SpeechBrain code for running experiments.

Authors
 * Peter Plantinga 2020
"""

import os
import sys
import torch
import shutil
import logging
import inspect
import argparse
import subprocess
import ruamel.yaml
import speechbrain as sb
import torch.distributed as dist
from io import StringIO
from datetime import date
from enum import Enum, auto
from tqdm.contrib import tqdm
from types import SimpleNamespace
from torch.nn.parallel import DistributedDataParallel as DDP

logger = logging.getLogger(__name__)
DEFAULT_LOG_CONFIG = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LOG_CONFIG = os.path.join(DEFAULT_LOG_CONFIG, "log-config.yaml")


def create_experiment_directory(
    experiment_directory,
    hyperparams_to_save=None,
    overrides={},
    log_config=DEFAULT_LOG_CONFIG,
    save_env_desc=True,
):
    """Create the output folder and relevant experimental files.

    Arguments
    ---------
    experiment_directory : str
        The place where the experiment directory should be created.
    hyperparams_to_save : str
        A filename of a yaml file representing the parameters for this
        experiment. If passed, references are resolved and the result is
        written to a file in the experiment directory called "hyperparams.yaml"
    overrides : dict
        A mapping of replacements made in the yaml file, to save in yaml.
    log_config : str
        A yaml filename containing configuration options for the logger.
    save_env_desc : bool
        If True, an environment state description is saved to the experiment
        directory, in a file called env.log in the experiment directory
    """
    if not os.path.isdir(experiment_directory):
        os.makedirs(experiment_directory)

    # Write the parameters file
    if hyperparams_to_save is not None:
        hyperparams_filename = os.path.join(
            experiment_directory, "hyperparams.yaml"
        )
        with open(hyperparams_to_save) as f:
            resolved_yaml = sb.resolve_references(f, overrides)
        with open(hyperparams_filename, "w") as w:
            print("# Generated %s from:" % date.today(), file=w)
            print("# %s" % os.path.abspath(hyperparams_to_save), file=w)
            print("# yamllint disable", file=w)
            shutil.copyfileobj(resolved_yaml, w)

    # Copy executing file to output directory
    module = inspect.getmodule(inspect.currentframe().f_back)
    if module is not None:
        callingfile = os.path.realpath(module.__file__)
        shutil.copy(callingfile, experiment_directory)

    # Log exceptions to output automatically
    log_file = os.path.join(experiment_directory, "log.txt")
    logger_overrides = {"handlers": {"file_handler": {"filename": log_file}}}
    sb.setup_logging(log_config, logger_overrides)
    sys.excepthook = _logging_excepthook

    # Log beginning of experiment!
    logger.info("Beginning experiment!")
    logger.info(f"Experiment folder: {experiment_directory}")
    commit_hash = subprocess.check_output(["git", "describe", "--always"])
    logger.debug("Commit hash: '%s'" % commit_hash.decode("utf-8").strip())

    # Save system description:
    if save_env_desc:
        description_str = sb.get_environment_description()
        with open(os.path.join(experiment_directory, "env.log"), "w") as fo:
            fo.write(description_str)


def _logging_excepthook(exc_type, exc_value, exc_traceback):
    """Interrupt exception raising to log the error."""
    logger.error("Exception:", exc_info=(exc_type, exc_value, exc_traceback))


def parse_arguments(arg_list):
    r"""Parse command-line arguments to the experiment.

    Arguments
    ---------
    arg_list: list
        a list of arguments to parse, most often from `sys.argv[1:]`

    Returns
    -------
    param_file : str
        The location of the parameters file.
    overrides : str
        The yaml-formatted overrides, to pass to ``load_extended_yaml``.

    Example
    -------
    >>> argv = ['hyperparams.yaml', '--seed', '10']
    >>> filename, overrides = parse_arguments(argv)
    >>> filename
    'hyperparams.yaml'
    >>> overrides
    'seed: 10\n'
    """
    parser = argparse.ArgumentParser(
        description="Run a SpeechBrain experiment",
    )
    parser.add_argument(
        "param_file",
        help="a yaml-formatted file using the extended YAML syntax "
        "defined by SpeechBrain.",
    )
    parser.add_argument(
        "--yaml_overrides",
        help="A yaml-formatted string representing a dictionary of "
        "overrides to the parameters in the param file. The keys of "
        "the dictionary can use dots to represent levels in the yaml "
        'hierarchy. For example: "{model.param1: value1}" would '
        "override the param1 parameter of the model node.",
    )
    parser.add_argument(
        "--output_folder",
        help="A folder for storing all experiment-related outputs.",
    )
    parser.add_argument(
        "--data_folder", help="A folder containing the data used for training",
    )
    parser.add_argument(
        "--save_folder",
        help="A folder for storing checkpoints that allow restoring "
        "progress for testing or re-starting training.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="A random seed to reproduce experiments on the same machine",
    )
    parser.add_argument(
        "--log_config",
        help="A file storing the configuration options for logging",
    )

    # Ignore items that are "None", they were not passed
    parsed_args = vars(parser.parse_args(arg_list))

    param_file = parsed_args["param_file"]
    del parsed_args["param_file"]

    # Convert yaml_overrides to dictionary
    yaml_overrides = ""
    if parsed_args["yaml_overrides"] is not None:
        yaml_overrides = parsed_args["yaml_overrides"]
        del parsed_args["yaml_overrides"]

    # Only return non-empty items
    items = {k: v for k, v in parsed_args.items() if v is not None}

    # Convert to string and append to overrides
    ruamel_yaml = ruamel.yaml.YAML()
    overrides = ruamel_yaml.load(yaml_overrides) or {}
    sb.recursive_update(overrides, items)
    yaml_stream = StringIO()
    ruamel_yaml.dump(overrides, yaml_stream)

    return param_file, yaml_stream.getvalue()


def ddp_init(rank, brain, args):
    print(f"Process {rank} reporting in!")
    os.environ["MASTER_ADDR"] = "localhost"
    os.environ["MASTER_PORT"] = "12321"
    dist.init_process_group(
        backend="nccl", world_size=brain.ddp_procs, rank=rank
    )
    brain.device = rank
    brain.root_process = rank == 0

    # force the models to start and remain synchronized
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False

    args = list(args)
    sampler = torch.utils.data.DistributedSampler
    args[1] = args[1](sampler, brain.ddp_procs, rank=rank)
    args[2] = args[2](sampler, brain.ddp_procs, rank=rank)

    # Wrap modules with DDP
    for name, hparam in brain.hparams.__dict__.items():
        if isinstance(hparam, torch.nn.Module):
            hparam = torch.nn.SyncBatchNorm.convert_sync_batchnorm(hparam)
            hparam = hparam.to(rank)
            if any(p.requires_grad for p in hparam.parameters()):
                hparam = DDP(hparam, device_ids=[rank])
                setattr(brain.hparams, name, hparam)

    brain._fit(*args)


class Stage(Enum):
    """Simple enum to track stage of experiments."""

    TRAIN = auto()
    VALID = auto()
    TEST = auto()


class Brain:
    r"""Brain class abstracts away the details of data loops.

    The primary purpose of the `Brain` class is the implementation of
    the ``fit()`` method, which iterates epochs and datasets for the
    purpose of "fitting" a set of modules to a set of data.

    In order to use the ``fit()`` method, one should sub-class the ``Brain``
    class and override any methods for which the default behavior does not
    match the use case. For a simple use case (e.g. training a single model
    with a single dataset) the only methods that need to be overridden are:

    * ``compute_forward()``
    * ``compute_objectives()``

    The example below illustrates how overriding these two methods is done.

    For more complicated use cases, such as multiple modules that need to
    be updated, the following methods can be overridden:

    * ``fit_batch()``
    * ``evaluate_batch()``

    Arguments
    ---------
    hparams : dict
        Each key:value pair should consist of a string key and a hyperparameter
        that is used within the overridden methods. The hyperparameters can
        be any python object, including torch modules which will be moved
        to the device specified in the ``device`` argument. These will
        be accessible via an ``hparams`` attribute, using "dot" notation:
        e.g. self.hparams.model(x)
    opt_class : torch.optim class
        A torch optimizer constructor that has takes only the list of
        parameters (e.g. a lambda or partial function definition). By default,
        this will be passed all modules in hparams and jit_modules at the
        beginning of the ``fit()`` method. This behavior can be changed
        by overriding the ``configure_optimizers()`` method.
    jit_modules : dict of str:torch.nn.Module pairs
        ``torch.nn.Module`` object(s) to pass to ``torch.jit.script``. As
        with the hparams and optim arguments, these are made accessible with
        a ``jit_modules`` attribute, using "dot" notation:
        e.g. self.jit_modules.model(x)
    checkpointer : speechbrain.Checkpointer
        By default, this will be used to load checkpoints, and will have the
        optimizer added to continue training if interrupted.
    device : str
        The location for performing computations.
    ddp_procs : int
        Number of processes to use with torch's ``DistributedDataParallel``.
        if passed, this will assume there is one GPU per process.
    auto_mix_prec: bool
        If True, automatic mixed-precision is used. Activate it only with cuda.

    Example
    -------
    >>> from torch.optim import SGD
    >>> class SimpleBrain(Brain):
    ...     def compute_forward(self, x, stage):
    ...         return self.hparams.model(x)
    ...     def compute_objectives(self, predictions, targets, stage):
    ...         return torch.nn.functional.l1_loss(predictions, targets)
    >>> model = torch.nn.Linear(in_features=10, out_features=10)
    >>> brain = SimpleBrain({"model": model}, opt_class=lambda x: SGD(x, 0.1))
    >>> brain.fit(range(1), ([torch.rand(10, 10), torch.rand(10, 10)],))
    """

    def __init__(
        self,
        hparams,
        opt_class=None,
        jit_modules=None,
        checkpointer=None,
        device="cpu",
        ddp_procs=0,
        auto_mix_prec=False,
    ):
        self.opt_class = opt_class
        self.jit_modules = jit_modules
        self.checkpointer = checkpointer
        self.device = device
        self.ddp_procs = ddp_procs
        self.auto_mix_prec = auto_mix_prec

        self.root_process = True
        modulelist = []

        # Put modules onto correct device
        for name, hparam in hparams.items():
            if isinstance(hparam, torch.nn.Module):
                hparam = hparam.to(self.device)
                hparams[name] = hparam
                modulelist.append(hparam)

        # Make hyperparams available with simple "dot" notation
        self.hparams = SimpleNamespace(**hparams)

        # Append JIT modules here just for accurate parameter count.
        # JIT modules are compiled and converted to "dot" notation when fit()
        # is called, since compiled modules can't be pickled for DDP.
        if jit_modules is not None:
            for name, module in jit_modules.items():
                modulelist.append(module)

        # Store modules as ModuleList, primarily for calling train()/eval()
        self.modules = torch.nn.ModuleList(modulelist)

        # Automatic mixed precision init
        if self.auto_mix_prec:
            self.scaler = torch.cuda.amp.GradScaler()

        # List parameter count for the user
        total_params = sum(
            p.numel() for p in self.modules.parameters() if p.requires_grad
        )
        if total_params > 0:
            clsname = self.__class__.__name__
            fmt_num = sb.format_order_of_magnitude(total_params)
            logger.info(f"{fmt_num} trainable parameters in {clsname}")

    def compute_forward(self, x, stage):
        """Forward pass, to be overridden by sub-classes.

        Arguments
        ---------
        x : torch.Tensor or list of tensors
            The input tensor or tensors for processing.
        stage : Stage
            The stage of the experiment: Stage.TRAIN, Stage.VALID, Stage.TEST

        Returns
        -------
        torch.Tensor
            A tensor representing the outputs after all processing is complete.
        """
        raise NotImplementedError

    def compute_objectives(self, predictions, targets, stage):
        """Compute loss, to be overridden by sub-classes.

        Arguments
        ---------
        predictions : torch.Tensor or list of tensors
            The output tensor or tensors to evaluate.
        targets : torch.Tensor or list of tensors
            The gold standard to use for evaluation.
        stage : Stage
            The stage of the experiment: Stage.TRAIN, Stage.VALID, Stage.TEST

        Returns
        -------
        loss : torch.Tensor
            A tensor with the computed loss
        """
        raise NotImplementedError

    def on_stage_start(self, stage, epoch=None):
        """Gets called when a stage starts.

        Useful for defining class variables used during the stage.

        Arguments
        ---------
        stage : Stage
            The stage of the experiment: Stage.TRAIN, Stage.VALID, Stage.TEST
        epoch : int
            The current epoch count.
        """
        pass

    def on_stage_end(self, stage, stage_loss, epoch=None):
        """Gets called at the end of a stage.

        Arguments
        ---------
        stage : Stage
            The stage of the experiment: Stage.TRAIN, Stage.VALID, Stage.TEST
        stage_loss : float
            The average loss over the completed stage.
        epoch : int
            The current epoch count.
        """
        pass

    def on_fit_start(self):
        """Gets called at the beginning of ``fit()``, on multiple processes
        if ddp_procs is more than 0.

        Default implementation compiles the jit modules, initializes
        optimizers, and loads the latest checkpoint to resume training.
        """
        # Run this *after* mp.spawn since jit modules cannot be pickled.
        self.compile_jit()

        # Initialize optimizers after parameters are configured
        self.init_optimizers()

        # Load latest checkpoint to resume training if interrupted
        if self.checkpointer is not None:
            self.checkpointer.recover_if_possible()

    def init_optimizers(self):
        """Called during ``on_fit_start()``, initialize optimizers
        after parameters are fully configured (e.g. DDP, jit).

        The default implementation of this method depends on an optimizer
        class being passed at initialization that takes only a list
        of parameters (e.g. a lambda or a partial function definition).
        This creates a single optimizer that optimizes all trainable params.

        Override this class if there are multiple optimizers.
        """
        if self.opt_class is not None:
            params = []
            for name, hparam in self.hparams.__dict__.items():
                if isinstance(hparam, torch.nn.Module):
                    if any(p.requires_grad for p in hparam.parameters()):
                        params.extend(hparam.parameters())

            if self.jit_modules is not None:
                for name, jit_module in self.jit_modules.__dict__.items():
                    params.extend(jit_module.parameters())

            self.optimizer = self.opt_class(params)

            if self.checkpointer is not None:
                self.checkpointer.add_recoverable("optimizer", self.optimizer)

    def on_evaluate_start(self, max_key=None, min_key=None):
        """Gets called at the beginning of ``evaluate()``

        Default implementation loads the best-performing checkpoint for
        evaluation, based on stored metrics.

        Arguments
        ---------
        max_key : str
            Key to use for finding best checkpoint (higher is better).
            By default, passed to ``self.checkpointer.recover_if_possible()``.
        min_key : str
            Key to use for finding best checkpoint (lower is better).
            By default, passed to ``self.checkpointer.recover_if_possible()``.
        """

        # Recover best checkpoint for evaluation
        if self.checkpointer is not None:
            self.checkpointer.recover_if_possible(
                max_key=max_key, min_key=min_key
            )

    def fit_batch(self, batch):
        """Fit one batch, override to do multiple updates.

        The default impementation depends on a few methods being defined
        with a particular behavior:

        * ``compute_forward()``
        * ``compute_objectives()``

        Also depends on having optimizers passed at initialization.

        Arguments
        ---------
        batch : list of torch.Tensors
            batch of data to use for training. Default implementation assumes
            this batch has two elements: inputs and targets.

        Returns
        -------
        detached loss
        """
        inputs, labels = batch

        # Managing automatic mixed precision
        if self.auto_mix_prec:
            with torch.cuda.amp.autocast():
                outputs = self.compute_forward(inputs, Stage.TRAIN)
                loss = self.compute_objectives(outputs, labels, Stage.TRAIN)
                self.scaler.scale(loss).backward()
                self.scaler.step(self.optimizer)
                self.optimizer.zero_grad()
                self.scaler.update()
        else:
            outputs = self.compute_forward(inputs, Stage.TRAIN)
            loss = self.compute_objectives(outputs, labels, Stage.TRAIN)
            loss.backward()
            self.optimizer.step()
            self.optimizer.zero_grad()

        return loss.detach().cpu()

    def evaluate_batch(self, batch, stage):
        """Evaluate one batch, override for different procedure than train.

        The default impementation depends on two methods being defined
        with a particular behavior:

        * ``compute_forward()``
        * ``compute_objectives()``

        Arguments
        ---------
        batch : list of torch.Tensors
            batch of data to use for evaluation. Default implementation assumes
            this batch has two elements: inputs and targets.
        stage : Stage
            The stage of the experiment: Stage.VALID, Stage.TEST

        Returns
        -------
        detached loss
        """
        inputs, targets = batch
        out = self.compute_forward(inputs, stage=stage)
        loss = self.compute_objectives(out, targets, stage=stage)
        return loss.detach().cpu()

    def fit(
        self, epoch_counter, train_set, valid_set=None, progressbar=True,
    ):
        """Iterate epochs and datasets to improve objective.

        Relies on the existence of mulitple functions that can (or should) be
        overridden. The following methods are used and expected to have a
        certain behavior:

        * ``fit_batch()``
        * ``evaluate_batch()``
        * ``update_average()``

        If the initialization was done with ddp_procs > 0, this
        method will spawn the correct number of processes and run a portion
        of the training data on the corresponding device.

        Arguments
        ---------
        epoch_counter : iterable
            each call should return an integer indicating the epoch count.
        train_set : DataLoader
            A set of data to use for training.
        valid_set : DataLoader
            A set of data to use for validation.
        progressbar : bool
            Whether to display the progress of each epoch in a progressbar.
        """
        if self.ddp_procs > 0:
            assert isinstance(
                train_set, sb.data_io.data_io.DataLoaderFactory
            ) and isinstance(
                valid_set, sb.data_io.data_io.DataLoaderFactory
            ), "train_set and valid set must be sb.data_io.data_io.DataLoaderFactory Object under DDP mode"
            self._ddp_fit(epoch_counter, train_set, valid_set, progressbar)
        else:
            self._fit(epoch_counter, train_set, valid_set, progressbar)

    def _ddp_fit(self, *args):
        torch.multiprocessing.spawn(ddp_init, (self, args), self.ddp_procs)

    def _fit(self, epoch_counter, train_set, valid_set, progressbar):

        self.on_fit_start()

        # Iterate epochs
        for epoch in epoch_counter:

            # Training stage
            self.on_stage_start(Stage.TRAIN, epoch)
            self.modules.train()
            avg_train_loss = 0.0

            # Only show progressbar if requested and root_process
            disable = not (progressbar and self.root_process)
            with tqdm(train_set, dynamic_ncols=True, disable=disable) as t:
                for i, batch in enumerate(t):
                    loss = self.fit_batch(batch)
                    avg_train_loss = self.update_average(
                        loss, avg_train_loss, iteration=i + 1
                    )
                    t.set_postfix(train_loss=avg_train_loss)
            self.on_stage_end(Stage.TRAIN, avg_train_loss, epoch)

            # Validation stage
            avg_valid_loss = None
            if valid_set is not None:
                self.on_stage_start(Stage.VALID, epoch)
                self.modules.eval()
                avg_valid_loss = 0.0
                with torch.no_grad():
                    for i, batch in enumerate(
                        tqdm(valid_set, dynamic_ncols=True, disable=disable)
                    ):
                        loss = self.evaluate_batch(batch, stage=Stage.VALID)
                        avg_valid_loss = self.update_average(
                            loss, avg_valid_loss, iteration=i + 1
                        )
                self.on_stage_end(Stage.VALID, avg_valid_loss, epoch)

    def compile_jit(self):
        """This should be run *after* mp.spawn, since jit modules
        cannot be pickled.
        """
        if self.jit_modules is None:
            return

        for name, module in self.jit_modules.items():
            module = torch.jit.script(module)
            module = module.to(self.device)

            # Wrap module with DDP when requested
            needs_grad = any(p.requires_grad for p in module.parameters())
            if needs_grad and self.ddp_procs > 0:
                module = DDP(module, device_ids=[self.device])

            self.modules.append(module)
            self.jit_modules[name] = module

        # Convert jit modules to use "dot" notation
        self.jit_modules = SimpleNamespace(**self.jit_modules)

    def evaluate(self, test_set, max_key=None, min_key=None, progressbar=True):
        """Iterate test_set and evaluate brain performance. By default, loads
        the best-performing checkpoint (as recorded using the checkpointer).

        Arguments
        ---------
        test_set : list of DataLoaders
            This list will be zipped before iterating.
        max_key : str
            Key to use for finding best checkpoint, passed to on_evaluate_start
        min_key : str
            Key to use for finding best checkpoint, passed to on_evaluate_start
        progressbar : bool
            Whether to display the progress in a progressbar.

        Returns
        -------
        average test loss
        """
        self.on_evaluate_start(max_key=max_key, min_key=min_key)
        self.on_stage_start(Stage.TEST, epoch=None)
        self.modules.eval()
        avg_test_loss = 0.0
        disable = not progressbar
        with torch.no_grad():
            for i, batch in enumerate(
                tqdm(test_set, dynamic_ncols=True, disable=disable)
            ):
                loss = self.evaluate_batch(batch, stage=Stage.TEST)
                avg_test_loss = self.update_average(
                    loss, avg_test_loss, iteration=i + 1
                )
        self.on_stage_end(Stage.TEST, avg_test_loss, epoch=None)

    def update_average(self, loss, avg_loss, iteration):
        """Update running average of the loss.

        Arguments
        ---------
        loss : torch.tensor
            detached loss, a single float value.
        avg_loss : float
            current running average.
        iteration : int
            The iteration count.

        Returns
        -------
        float
            The average loss
        """
        if not torch.isfinite(loss):
            raise ValueError(
                "Loss is not finite. To debug, wrap `fit()` with autograd's "
                "`detect_anomaly()`, e.g.\n\nwith "
                "torch.autograd.detect_anomaly():\n\tbrain.fit(...)"
            )

        # Compute moving average
        avg_loss -= avg_loss / iteration
        avg_loss += float(loss) / iteration
        return avg_loss
