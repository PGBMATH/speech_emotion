"""Encoding categorical data as integers

Authors
  * Samuele Cornell 2020
  * Aku Rouhe 2020
"""
import ast
import torch
import collections
import itertools
import logging
import speechbrain as sb

logger = logging.getLogger(__name__)

# NOTE: Changing these does NOT change the defaults in the classes.
# Consider these read-only.
DEFAULT_UNK = "<unk>"
DEFAULT_BOS = "<s>"
DEFAULT_EOS = "<s>"
DEFAULT_BLANK = "<b>"


class CategoricalEncoder:
    """
    Encode labels of a discrete set.

    Used for encoding e.g. speaker identities in speaker recognition.
    """

    VALUE_SEPARATOR = " => "
    EXTRAS_SEPARATOR = "================\n"

    def __init__(self, starting_index=0, **special_labels):
        self.lab2ind = {}
        self.ind2lab = {}
        self.starting_index = starting_index
        # NOTE: unk_label is not necessarily set at all!
        # This is because None is a suitable value for unk.
        # So the test is: hasattr(self, "unk_label")
        # rather than self.unk_label is not None
        if "unk_label" in special_labels:
            self.add_unk(special_labels["unk_label"])

    def __len__(self):
        return len(self.lab2ind)

    @classmethod
    def from_saved(cls, path):
        """Recreate a previously saved encoder directly"""
        obj = cls()
        obj.load(path)
        return obj

    def update_from_iterable(self, iterable, sequence_input=False):
        """Update from iterator

        Arguments
        ---------
        iterable : iterable
            Input sequence on which to operate.
        sequence_input : bool
            Whether iterable yields sequences of labels or individual labels
            directly. False by default.
        """
        if sequence_input:
            label_iterator = itertools.chain.from_iterable(iterable)
        else:
            label_iterator = iter(iterable)
        for label in label_iterator:
            self.ensure_label(label)

    def update_from_didataset(
        self, didataset, output_key, sequence_input=False
    ):
        """Update from DynamicItemDataset

        Arguments
        ---------
        didataset : DynamicItemDataset
            Dataset on which to operate.
        output_key : str
            Key in the dataset (in data or a dynamic item) to encode.
        sequence_input : bool
            Whether the data yielded with the specified key consists of
            sequences of labels or individual labels directly.
        """
        with didataset.output_keys_as([output_key]):
            self.update_from_iterable(
                (data_point[output_key] for data_point in didataset),
                sequence_input=sequence_input,
            )

    def update_from_transform_dataset(
        self, transform_dataset, output_key, sequence_input=False,
    ):
        with transform_dataset.output_keys_as([output_key]):
            self.update_from_iterable(
                (data_point[output_key] for data_point in transform_dataset),
                sequence_input=sequence_input,
            )

    def limited_labelset_from_iterable(
        self, iterable, sequence_input=False, n_most_common=None, min_count=1
    ):
        """Produce label mapping from iterable based on label counts

        Used to limit label set size.

        Arguments
        ---------
        iterable : iterable
            Input sequence on which to operate.
        sequence_input : bool
            Whether iterable yields sequences of labels or individual labels
            directly. False by default.
        n_most_common : int, None
            Take at most this many labels as the label set, keeping the most
            common ones. If None (as by default), take all.
        min_count : int
            Don't take labels if they appear less than this many times.

        Returns
        -------
        collections.Counter
            The counts of the different labels (unfiltered).
        """
        if self.lab2ind:
            clsname = self.__class__.__name__
            logger.info(
                f"Limited_labelset_from_iterable called, "
                f"but {clsname} is not empty. "
                "The new labels will be added, i.e. won't overwrite. "
                "This is normal if there is e.g. an unk label already."
            )
        if sequence_input:
            label_iterator = itertools.chain.from_iterable(iterable)
        else:
            label_iterator = iter(iterable)
        counts = collections.Counter(label_iterator)
        for label, count in counts.most_common(n_most_common):
            if count < min_count:
                # .most_common() produces counts in descending order,
                # so no more labels can be found
                break
            self.add_label(label)
        return counts

    def add_label(self, label):
        """Add new label to the encoder, at the next free position.

        Arguments
        ---------
        label : hashable
            Most often labels are str, but anything that can act as dict key is
            supported. Note that default save/load only supports Python
            literals.

        Returns
        -------
        int
            The index that was used to encode this label.
        """
        if label in self.lab2ind:
            clsname = self.__class__.__name__
            raise KeyError(f"Label already present in {clsname}")
        index = self._next_index()
        self.lab2ind[label] = index
        self.ind2lab[index] = label
        return index

    def ensure_label(self, label):
        """Add label if it is not already present.

        Arguments
        ---------
        label : hashable
            Most often labels are str, but anything that can act as dict key is
            supported. Note that default save/load only supports Python
            literals.

        Returns
        -------
        int
            The index that was used to encode this label.
        """
        if label in self.lab2ind:
            return self.lab2ind[label]
        else:
            return self.add_label(label)

    def insert_label(self, label, index):
        """Add a new label, forcing its index to a specific value.

        If a label already has the specified index, it is moved to the end
        of the mapping.

        Arguments
        ---------
        label : hashable
            Most often labels are str, but anything that can act as dict key is
            supported. Note that default save/load only supports Python
            literals.
        index : int
            The specific index to use.
        """
        if label in self.lab2ind:
            clsname = self.__class__.__name__
            raise KeyError(f"Label already present in {clsname}")
        else:
            self.enforce_label(label, index)

    def enforce_label(self, label, index):
        """Make sure label is present and encoded to particular index.

        If the label is present, but encoded to some other index, it is
        moved to the given index.

        If there is already another label at the
        given index, that label is moved to the next free position.
        """
        index = int(index)
        if label in self.lab2ind:
            if index == self.lab2ind[label]:
                return
            else:
                # Delete old index mapping. Everything else gets overwritten.
                del self.ind2lab[self.lab2ind[label]]
        # Move other label out of the way:
        if index in self.ind2lab:
            saved_label = self.ind2lab[index]
            moving_other = True
        else:
            moving_other = False
        # Ready to push the new index.
        self.lab2ind[label] = index
        self.ind2lab[index] = label
        # And finally put the moved index in new spot.
        if moving_other:
            logger.info(
                f"Moving label {repr(saved_label)} from index "
                f"{index}, because {repr(label)} was put at its place."
            )
            new_index = self._next_index()
            self.lab2ind[saved_label] = new_index
            self.ind2lab[new_index] = saved_label

    def add_unk(self, unk_label=DEFAULT_UNK):
        """Add label for unknown tokens (out-of-vocab)

        When asked to encode unknown labels, they can be mapped to this.

        Arguments
        ---------
        label : hashable, optional
            Most often labels are str, but anything that can act as dict key is
            supported. Note that default save/load only supports Python
            literals. Default: <unk>. This can be None, as well!

        Returns
        -------
        int
            The index that was used to encode this.
        """
        self.unk_label = unk_label
        return self.add_label(unk_label)

    def _next_index(self):
        """The index to use for the next new label"""
        index = self.starting_index
        while index in self.ind2lab:
            index += 1
        return index

    def is_continuous(self):
        """Check that the set of indices doesn't have gaps

        For example:
        If starting index = 1
        Continuous: [1,2,3,4]
        Continuous: [0,1,2]
        Non-continuous: [2,3,4]
        Non-continuous: [1,2,4]

        Returns
        -------
        bool
            True if continuous.
        """
        # Because of Python indexing this also handles the special cases
        # of 0 or 1 labels.
        indices = sorted(self.ind2lab.keys())
        return self.starting_index in indices and all(
            j - i == 1 for i, j in zip(indices[:-1], indices[1:])
        )

    def encode_label(self, label, allow_unk=True):
        """Encode label to int

        Arguments
        ---------
        label : hashable
            Label to encode, must exist in the mapping.
        allow_unk : bool
            If given label is not in the label set
            AND unk_label has been added with add_unk(),
            allows encoding to unk_label's index.

        Returns
        -------
        int
            Corresponding encoded int value.
        """
        try:
            return self.lab2ind[label]
        except KeyError:
            if hasattr(self, "unk_label") and allow_unk:
                return self.lab2ind[self.unk_label]
            elif hasattr(self, "unk_label") and not allow_unk:
                raise KeyError(
                    f"Unknown label {label}, and explicitly "
                    "disallowed the use of the existing unk-label"
                )
            elif not hasattr(self, "unk_label") and allow_unk:
                raise KeyError(
                    f"Cannot encode unknown label {label}. "
                    "You have not called add_unk() to add a special "
                    "unk-label for unknown labels."
                )
            else:
                raise KeyError(
                    f"Couldn't and wouldn't encode unknown label " f"{label}."
                )

    def encode_label_torch(self, label, allow_unk=True):
        """Encode label to torch.LongTensor

        Arguments
        ---------
        label : hashable
            Label to encode, must exist in the mapping.

        Returns
        -------
        torch.LongTensor
            Corresponding encoded int value.
            Tensor shape [1]
        """
        return torch.LongTensor(self.encode_label(label, allow_unk))

    def encode_sequence(self, sequence, allow_unk=True):
        """Encode a sequence of labels to list

        Arguments
        ---------
        x : iterable
            Labels to encode, must exist in the mapping.

        Returns
        -------
        list
            Corresponding integer labels
        """
        return [self.encode_label(label, allow_unk) for label in sequence]

    def encode_sequence_torch(self, sequence, allow_unk=True):
        """Encode a sequence of labels to torch.LongTensor

        Arguments
        ---------
        x : iterable
            Labels to encode, must exist in the mapping.

        Returns
        -------
        torch.LongTensor
            Corresponding integer labels
            Tensor shape [len(sequence)]
        """
        return torch.LongTensor(
            [self.encode_label(label, allow_unk) for label in sequence]
        )

    def decode_torch(self, x):
        """Decodes an arbitrarily nested torch.Tensor to a list of labels.

        Provided separately because Torch provides clearer introspection,
        and so doesn't require try-except.

        Arguments
        ---------
        x : torch.Tensor
            Torch tensor of some integer dtype (Long, int) and any shape to
            decode.

        Returns
        -------
        list
            list of original labels
        """
        decoded = []
        # Recursively operates on the different dimensions.
        if x.ndim == 1:  # Last dimension!
            for element in x:
                decoded.append(self.ind2lab[int(element)])
        else:
            for subtensor in x:
                decoded.append(self.decode_torch(subtensor))
        return decoded

    def decode_ndim(self, x):
        """Decodes an arbitrarily nested iterable to a list of labels.

        This works for essentially any pythonic iterable (including torch), and
        also single elements.

        Arguments
        ---------
        x : Any
            Python list or other iterable or torch.Tensor or a single integer element

        Returns
        -------
        list, Any
            ndim list of original labels, or if input was signle element,
            output will be, too.
        """
        # Recursively operates on the different dimensions.
        try:
            decoded = []
            for subtensor in x:
                decoded.append(self.decode_ndim(subtensor))
            return decoded
        except TypeError:  # Not an iterable, bottom level!
            return self.ind2lab[int(x)]

    def save(self, path):
        """Save the categorical encoding for later use and recovery

        Saving uses a Python literal format, which supports things like
        tuple labels, but is considered safe to load (unlike e.g. pickle).

        Arguments
        ---------
        path : str, Path
            Where to save. Will overwrite.
        """
        extras = self._get_extras()
        try:
            if sb.if_main_process():
                self._save_literal(path, self.lab2ind, extras)
        finally:
            sb.ddp_barrier()

    def load(self, path):
        """Loads from the given path

        CategoricalEncoder uses a Python literal format, which supports things
        like tuple labels, but is considered safe to load (unlike e.g. pickle).

        Arguments
        ---------
        path : str, Path
            Where to load from.
        """
        if self.lab2ind:
            clsname = self.__class__.__name__
            logger.info(
                f"Load called, but {clsname} is not empty. "
                "Loaded data will overwrite everything. "
                "This is normal if there is e.g. an unk label defined at init."
            )
        lab2ind, ind2lab, extras = self._load_literal(path)
        self.lab2ind = lab2ind
        self.ind2lab = ind2lab
        self._set_extras(extras)
        # If we're here, load was a success!
        logger.debug(f"Loaded categorical encoding from {path}")

    def _load_if_possible(self, path):
        """Loads if possible, returns bool indicating if loaded or not.

        Arguments
        ---------
        path : str, Path
            Where to load from.

        Returns
        -------
        bool :
            If load was successful.

        Example
        -------
        >>> encoding_file = getfixture('tmpdir') / "encoding.txt"
        >>> encoder = CategoricalEncoder()
        >>> # The idea is in an experiment script to have something like this:
        >>> if not encoder.load_if_possible(encoding_file):
        ...     encoder.update_from_iterable("abcd")
        ...     encoder.save(encoding_file)
        >>> # So the first time you run the experiment, the encoding is created.
        >>> # However, later, the encoding exists:
        >>> encoder = CategoricalEncoder()
        >>> if not encoder.load_if_possible(encoding_file):
        ...     assert False  # We won't get here!
        >>> encoder.decode_ndim(range(4))
        ['a', 'b', 'c', 'd']
        """
        try:
            self.load(path)
        except FileNotFoundError:
            logger.debug(
                f"Would load categorical encoding from {path}, "
                "but file doesn't exist yet."
            )
            return False
        except (ValueError, SyntaxError):
            logger.debug(
                f"Would load categorical encoding from {path}, "
                "and file existed but seems to be corrupted or otherwise couldn't load."
            )
            return False
        return True  # If here, all good

    def load_if_possible(self, path):
        try:
            # all writing command must be done with the main_process
            bool_load = self._load_if_possible(path)
        finally:
            # wait for main_process if ddp is used
            sb.ddp_barrier()
            return bool_load

    def _get_extras(self):
        """Override this to provide any additional things to save

        Call super()._get_extras() to get the base extras
        """
        extras = {"starting_index": self.starting_index}
        if hasattr(self, "unk_label"):
            extras["unk_label"] = self.unk_label
        return extras

    def _set_extras(self, extras):
        """Override this to e.g. load any extras needed

        Call super()._set_extras(extras) to set the base extras
        """
        if "unk_label" in extras:
            self.unk_label = extras["unk_label"]
        self.starting_index = extras["starting_index"]

    @staticmethod
    def _save_literal(path, lab2ind, extras):
        """Save which is compatible with _load_literal"""
        with open(path, "w") as f:
            for label, ind in lab2ind.items():
                f.write(
                    repr(label)
                    + CategoricalEncoder.VALUE_SEPARATOR
                    + str(ind)
                    + "\n"
                )
            f.write(CategoricalEncoder.EXTRAS_SEPARATOR)
            for key, value in extras.items():
                f.write(
                    repr(key)
                    + CategoricalEncoder.VALUE_SEPARATOR
                    + repr(value)
                    + "\n"
                )
            f.flush()

    @staticmethod
    def _load_literal(path):
        """Load which supports Python literals as keys.

        This is considered safe for user input, as well (unlike e.g. pickle).
        """
        lab2ind = {}
        ind2lab = {}
        extras = {}
        with open(path) as f:
            # Load the label to index mapping (until EXTRAS_SEPARATOR)
            for line in f:
                if line == CategoricalEncoder.EXTRAS_SEPARATOR:
                    break
                literal, ind = line.strip().split(
                    CategoricalEncoder.VALUE_SEPARATOR, maxsplit=1
                )
                ind = int(ind)
                label = ast.literal_eval(literal)
                lab2ind[label] = ind
                ind2lab[ind] = label
            # Load the extras:
            for line in f:
                literal_key, literal_value = line.strip().split(
                    CategoricalEncoder.VALUE_SEPARATOR, maxsplit=1
                )
                key = ast.literal_eval(literal_key)
                value = ast.literal_eval(literal_value)
                extras[key] = value
        return lab2ind, ind2lab, extras


class TextEncoder(CategoricalEncoder):
    def __init__(self, starting_index=0, **special_labels):
        super().__init__(starting_index, **special_labels)
        # NOTE: bos_label and eos_label are not set at all!
        # This is because None is a suitable value.
        # So the test is: hasattr(self, "bos_label")
        # rather than self.bos_label is not None
        # Same thing with unk, see base class.
        if "bos_label" in special_labels and "eos_label" in special_labels:
            self.insert_bos_eos(
                special_labels["bos_label"], special_labels["eos_label"]
            )
        elif "bos_label" in special_labels or "eos_label" in special_labels:
            raise TypeError("Only BOS or EOS specified. Need both for init.")

    def add_bos_eos(
        self, bos_label=DEFAULT_BOS, eos_label=DEFAULT_EOS,
    ):
        """Add sentence boundary markers in the label set

        If the beginning-of-sentence and end-of-sentence markers
        are the same, will just use one sentence-boundary label.

        This method adds to the end of the index, rather than at the beginning,
        like insert_bos_eos.

        Arguments
        ---------
        bos_label : hashable
            Beginning-of-sentence label, any label
        eos_label : hashable
            End-of-sentence label, any label. If set to the same label as
            bos_label, will just use one sentence-boundary label.
        """
        if bos_label == eos_label:
            logger.debug(
                "BOS and EOS labels are the same so using just one sentence "
                "boundary label"
            )
            self.add_label(bos_label)
        else:
            self.add_label(bos_label)
            self.add_label(eos_label)
        self.bos_label = bos_label
        self.eos_label = eos_label

    def insert_bos_eos(
        self, bos_label=DEFAULT_BOS, eos_label=DEFAULT_EOS, bos_index=0
    ):
        """Insert sentence boundary markers in the label set.

        If the beginning-of-sentence and end-of-sentence markers
        are the same, will just use one sentence-boundary label.

        Arguments
        ---------
        bos_label : hashable
            Beginning-of-sentence label, any label
        eos_label : hashable
            End-of-sentence label, any label. If set to the same label as
            bos_label, will just use one sentence-boundary label.
        bos_index : int
            Where to insert bos_label. If EOS is added, it is added at
            box_index + 1.
        """
        if bos_label == eos_label:
            logger.debug(
                "BOS and EOS labels are the same so using just one sentence "
                "boundary label"
            )
            self.insert_label(bos_label, bos_index)
        else:
            self.insert_label(bos_label, bos_index)
            self.insert_label(eos_label, bos_index + 1)
        self.bos_label = bos_label
        self.eos_label = eos_label

    def prepend_bos_label(self, x):
        """Returns a list version of x, with BOS prepended"""
        if not hasattr(self, "bos_label"):
            raise KeyError("BOS label has not been added to label set!")
        return [self.bos_label] + list(x)

    def prepend_bos_index(self, x):
        """Returns a list version of x, with BOS index prepended"""
        if not hasattr(self, "bos_label"):
            raise KeyError("BOS label has not been added to label set!")
        return [self.lab2ind[self.bos_label]] + list(x)

    def append_eos_label(self, x):
        """Returns a list version of x, with EOS appended"""
        if not hasattr(self, "eos_label"):
            raise KeyError("EOS label has not been added to label set!")
        return list(x) + [self.eos_label]

    def append_eos_index(self, x):
        """Returns a list version of x, with EOS index appended"""
        if not hasattr(self, "eos_label"):
            raise KeyError("EOS label has not been added to label set!")
        return list(x) + [self.lab2ind[self.eos_label]]

    def _get_extras(self):
        extras = super()._get_extras()
        if hasattr(self, "bos_label"):
            extras["bos_label"] = self.bos_label
        if hasattr(self, "eos_label"):
            extras["eos_label"] = self.eos_label
        return extras

    def _set_extras(self, extras):
        super()._set_extras(extras)
        if "bos_label" in extras:
            self.bos_label = extras["bos_label"]
        if "eos_label" in extras:
            self.eos_label = extras["eos_label"]


class CTCTextEncoder(TextEncoder):
    def __init__(self, starting_index=0, **special_labels):
        super().__init__(starting_index, **special_labels)
        if "blank_label" in special_labels:
            self.insert_blank(special_labels["blank_label"])
        # NOTE: blank_label is not necessarily set at all!
        # This is because None is a suitable value.
        # So the test is: hasattr(self, "blank_label")
        # rather than self.blank_label is not None
        # Same thing with unk, see base class.

    def add_blank(self, blank_label=DEFAULT_BLANK):
        """Add blank symbol to labelset"""
        self.add_label(blank_label)
        self.blank_label = blank_label

    def insert_blank(self, blank_label=DEFAULT_BLANK, index=0):
        """Insert blank symbol at a given labelset"""
        self.insert_label(blank_label, index)
        self.blank_label = blank_label

    def collapse_labels(self, x, merge_repeats=True):
        """Applies the CTC collapsing rules on one label sequence

        Arguments
        ---------
        x : iterable
            Label sequence on which to operate.
        merge_repeats : bool
            Whether to merge repeated labels before removing blanks.
            In the basic CTC label topology, repeated labels are merged.
            However, in RNN-T, they are not.

        Returns
        -------
        list
            List of labels with collapsing rules applied.
        """
        # This cannot work on arbitrary "ndim", because strings can be
        # infinitely iterated. Iterating "a" produces "a" over and over again.
        if not hasattr(self, "blank_label"):
            raise KeyError("Blank label has not been added")
        if merge_repeats:
            return [
                label
                for i, label in enumerate(x)
                if (i == 0 or label != x[i - 1]) and label != self.blank_label
            ]
        else:
            return [label for label in x if label != self.blank_label]

    def collapse_indices_ndim(self, x, merge_repeats=True):
        """Applies the CTC collapsing rules on arbitrarily label sequence

        Arguments
        ---------
        x : iterable
            Label sequence on which to operate.
        merge_repeats : bool
            Whether to merge repeated labels before removing blanks.
            In the basic CTC label topology, repeated labels are merged.
            However, in RNN-T, they are not.

        Returns
        -------
        list
            List of labels with collapsing rules applied.
        """
        if not hasattr(self, "blank_label"):
            raise KeyError("Blank label has not been added")
        # Recursively operates on the different dimensions.
        collapsed = []
        for subtensor in x:
            try:
                collapsed.append(
                    self.collapse_indices_ndim(subtensor, merge_repeats)
                )
            except TypeError:  # Not an iterable at next level!
                # So we should rather operate on this dimension.
                break
        else:  # For-else: only enter else if NO break.
            return collapsed
        # We get here if we DID break:
        blank_index = self.lab2ind[self.blank_label]
        if merge_repeats:
            return [
                index
                for i, index in enumerate(x)
                if (i == 0 or index != x[i - 1]) and index != blank_index
            ]
        else:
            return [index for index in x if index != blank_index]

    def _get_extras(self):
        extras = super()._get_extras()
        if hasattr(self, "blank_label"):
            extras["blank_label"] = self.blank_label
        return extras

    def _set_extras(self, extras):
        super()._set_extras(extras)
        if "blank_label" in extras:
            self.blank_label = extras["blank_label"]
