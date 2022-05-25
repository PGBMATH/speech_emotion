"""Tests for checking consistency between yaml files and their corresponding training scripts.

Authors
 * Mirco Ravanelli 2022
 * Andreas Nautsch 2022
"""

import os
import re


def get_yaml_var(hparam_file):
    """Extracts from the input yaml file (hparams_file) the list of variables that
    should be used in the script file.

    Arguments
    ---------
    hparam_file : path
        Path of the yaml file containing the hyperparameters.

    Returns
    -------
    var_list: list
        list of the variables declared in the yaml file (sub-variables are not
        included).
    """
    var_lst = []
    with open(hparam_file) as f:
        for line in f:
            # Avoid empty lists or comments
            if len(line) <= 1 or line.startswith("#"):
                continue
            # Remove trailing characters
            line = line.rstrip()

            # Check for variables (e.g., 'key:' or '- !ref')
            if line.find(":") != -1 or line.find("- !ref") != -1:
                var_name = line[: line.find(":")]
                # The variables to check are like "key:" (we do not need to check
                # subvariavles as " key:")
                if not (
                    var_name[0] == " "
                    or var_name[0] == "\t"
                    or "!!" in line
                    or "!apply" in line
                ):
                    var_lst.append(var_name)
                # Check for the reference pattern
                # note: output_folder: !ref results/<experiment_name>/<seed> pattern
                if line.find("!ref") != -1:
                    # Check for 'annotation_list_to_check: [!ref <train_csv>, !ref <valid_csv>]' pattern
                    for subline in line.split('<'):
                        sub_var = subline[: subline.find(">")]
                        # Check for 'models[generator]' pattern (dictionary reference)
                        dict_pos = sub_var.find('[')
                        if dict_pos != -1:
                            sub_var = sub_var[:dict_pos]
                        # Remove variables already used in yaml
                        if sub_var in var_lst:
                            var_lst.remove(sub_var)
    return var_lst


def check_yaml_vs_script(hparam_file, script_file):
    """Checks consistency between the given yaml file (hparams_file) and the
    script file. The function detects if there are variables declared in the yaml
    file, but not used in the script file.

    Arguments
    ---------
    hparam_file : path
        Path of the yaml file containing the hyperparameters.
    script_file : path
        Path of the script file (.py) containing the training recipe.

    Returns
    -------
    Bool
        This function returns False if some mismatch is detected and True otherwise.
        A warning is raised to inform about which variable has been declared but
        not used.
    """
    print("Checking %s..." % (hparam_file))

    # Check if files exist
    if not (os.path.exists(hparam_file)):
        print("File %s not found!" % (hparam_file,))
        return False

    if not (os.path.exists(script_file)):
        print("File %s not found!" % (script_file,))
        return False

    # Getting list of variables declared in yaml
    var_lst = get_yaml_var(hparam_file)

    # Detect which of these variables are used in the script file
    detected_var = []
    with open(script_file) as f:
        for line in f:
            for var in var_lst:
                # The pattern can be ["key"] or ".key"
                if '["' + var + '"]' in line:
                    if var not in detected_var:
                        detected_var.append(var)
                # case: hparams[f"{dataset}_annotation"] - only that structure at the moment
                re_match = re.search(r'\[f.\{.*\}(.*).\]', line)
                if re_match is not None:
                    if re_match.group(1) in var:
                        print("\t\tAlert: potential inconsistency %s maybe used in %s (or not)."
                              % (var, re_match.group(0)))
                        detected_var.append(var)
                if "." + var in line:
                    if var not in detected_var:
                        detected_var.append(var)

    # Check which variables are declared but not used
    default_run_opt_keys = ["debug", "debug_batches", "debug_epochs",
                            "device", "cpu", "data_parallel_backend",
                            "distributed_launch", "distributed_backend",
                            "find_unused_parameters", "jit_module_keys",
                            "auto_mix_prec", "max_grad_norm", "nonfinite_patience",
                            "noprogressbar", "ckpt_interval_minutes",
                            "grad_accumulation_factor", "optimizer_step_limit"]
    artefacts = ["progressbar", "compute_error", "gradient_clipping", "eos_threshold",
                 "data_folder_rirs", "pretrainer", "vocab_size"]  # TODO disregard?
    unused_vars = list(set(var_lst) - set(detected_var) - set(default_run_opt_keys) - set(artefacts))
    for unused_var in unused_vars:
        print(
            '\tWARNING: variable "%s" not used in %s!'
            % (unused_var, script_file)
        )

    if len(unused_vars) == 0:
        return True
    else:
        return False
