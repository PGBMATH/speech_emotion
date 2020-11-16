from torch.utils.data import Dataset


class SegmentedDataset(Dataset):
    """
    SegmentedDataset handles pre-segmented datasets.

    It takes in input a dict in which each entry is a single example.
    The example in its turn is represented by using a dict:
    e.g. {"wav_file": "/path/to/helloworld.wav" "words": ["hello", "world"]}.

    in data_fields a list of elements which one wants to be returned by this dataset class __getitem__
    method must be specifies e.g. data_fields = ["wav_file", "words"].
    Corresponding data_transformation can be specified for each element we want to return.
    Note that transformations include also reading data from a file:
    e.g. {"wav_file": read_wav} where read_wav is a suitable function we provide in data_io.py.

    Finally the specified elements are transformed an returned from __getitem__
    in a dict where the keys corresponds to the data_fields entried e.g. "wav_file".


     ---------
    Examples : dict
        Dictionary containing single examples (e.g. utterances).
    data_fields : (list, tuple)
        The class to use for updating the modules' parameters.
    data_transforms : dict
        Dictionary where data transforms for each field is specified.
    """

    def __init__(
        self,
        examples: dict,
        data_transforms=None,
        discard_longer=None,
        discard_shorter=None,
    ):

        self.data_transforms = data_transforms

        if not isinstance(self.data_transforms, list):
            raise TypeError(
                "data_transforms should be a list, got {}".format(
                    type(self.data_transforms)
                )
            )
        for k in self.data_transforms:
            if not callable(k):
                raise ValueError(
                    "Each element in data_transforms dict must be callable"
                )

        if discard_shorter:
            if "length" not in self.examples[self.examples.keys()[0]].keys():
                raise KeyError(
                    "If discard_shorter option wants to be used, "
                    "each example must have a 'length' key containing the length of the example."
                )

            examples = {
                k: v
                for k, v in examples.items()
                if examples[k]["length"] >= discard_shorter
            }

        if discard_longer:
            if "length" not in self.examples[self.examples.keys()[0]].keys():
                raise KeyError(
                    "If discard_shorter option wants to be used, "
                    "each example must have a 'length' key containing the length of the example."
                )

            examples = {
                k: v
                for k, v in examples.items()
                if examples[k]["length"] <= discard_longer
            }

        self.examples = examples
        self.ex_ids = list(self.examples.keys())

    def __len__(self):
        return len(self.ex_ids)

    def __getitem__(self, item):
        ex_id = self.ex_ids[item]
        c_ex = self.examples[ex_id]
        out = {"id": ex_id}

        for k in c_ex.keys():
            for t_pipeline in self.data_transforms:
                if t_pipeline.target == k:
                    pip_name = t_pipeline.name
                    out[pip_name] = t_pipeline(c_ex[k])

        return out
