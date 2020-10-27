import torch


def pad_right_to(
    tensor: torch.Tensor,
    target_shape: (list, tuple),
    mode="constant",
    value=0.0,
):
    """
    This function takes a torch tensor of arbitrary shape and pads it to target
    shape by appending values on the right.

    Parameters
    ----------
    tensor: input torch tensor
        Input tensor whose dimension we need to pad.
    target_shape: (list, tuple)
        Target shape we want for the target tensor its len must be equal to tensor.ndim
    mode: str
        Pad mode, please refer to torch.nn.functional.pad documentation.
    value: float
        Pad value, please refer to torch.nn.functional.pad documentation.

    Returns
    -------
    tensor: torch.Tensor
        Padded tensor
    valid_vals: list
        List containing proportion for each dimension of original, non-padded values

    """
    assert len(target_shape) == tensor.ndim

    pads = []
    valid_vals = []
    i = len(target_shape) - 1
    j = 0
    while i >= 0:
        assert (
            target_shape[i] >= tensor.shape[i]
        ), "Target shape must be >= original shape for every dim"
        pads.extend([0, target_shape[i] - tensor.shape[i]])
        valid_vals.append(tensor.shape[j] / target_shape[j])
        i -= 1
        j += 1

    tensor = torch.nn.functional.pad(tensor, pads, mode=mode, value=value)

    return tensor, valid_vals


def batch_pad_right(tensors: list, mode="constant", value=0.0):
    """
    Given a list of torch tensors it batches them together by padding to the right
    on each dimension in order to get same length for all.

    Parameters
    ----------
    tensors: list
        List of tensor we wish to pad together.
    mode: str
        Padding mode see torch.nn.functional.pad documentation.
    value: float
        Padding value see torch.nn.functional.pad documentation.

    Returns
    -------
    tensor: torch.Tensor
        Padded tensor
    valid_vals: list
        List containing proportion for each dimension of original, non-padded values

    """

    assert len(tensors), "Tensors list must not be empty"
    if len(tensors) == 1:
        return tensors[0].unsqueeze(0), [[1.0 for x in range(tensors[0].ndim)]]
    assert any(
        [tensors[i].ndim == tensors[0].ndim for i in range(1, len(tensors))]
    ), "All tensors must have same number of dimensions"

    # we gather the max length for each dimension
    max_shape = []
    for dim in range(tensors[0].ndim):
        max_shape.append(max([x.shape[dim] for x in tensors]))

    batched = []
    valid = []
    for t in tensors:
        padded, valid_percent = pad_right_to(
            t, max_shape, mode=mode, value=value
        )
        batched.append(padded)
        valid.append(valid_percent)

    batched = torch.stack(batched)

    return batched, valid
