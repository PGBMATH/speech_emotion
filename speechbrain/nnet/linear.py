"""Library implementing linear transformation.

Author
    Mirco Ravanelli 2020
"""

import torch
import logging
import torch.nn as nn

logger = logging.getLogger(__name__)


class Linear(torch.nn.Module):
    """Computes a linear transformation y = wx + b.

    Arguments
    ---------
    n_neurons : int
        it is the number of output neurons (i.e, the dimensionality of the
        output)
    bias : bool
        if True, the additive bias b is adopted.

    Example
    -------
    >>> lin_t = Linear(n_neurons=100)
    >>> inputs = torch.rand(10, 50, 40)
    >>> output = lin_t(inputs,init_params=True)
    >>> output.shape
    torch.Size([10, 50, 100])
    """

    def __init__(self, n_neurons, bias=True):
        super().__init__()
        self.n_neurons = n_neurons
        self.bias = bias

    def init_params(self, first_input):
        """
        Arguments
        ---------
        first_input : tensor
            A first input used for initializing the parameters.
        """
        if len(first_input.shape) == 3:
            fea_dim = first_input.shape[2]
        if len(first_input.shape) == 4:
            fea_dim = first_input.shape[2] * first_input.shape[3]

        self.w = nn.Linear(fea_dim, self.n_neurons, bias=self.bias)
        self.w.to(first_input.device)

    def forward(self, x, init_params=False):
        """Returns the linear transformation of input tensor.

        Arguments
        ---------
        x : torch.Tensor
            input to transform linearly.
        """
        if init_params:
            self.init_params(x)

        if len(x.shape) == 4:
            x = x.reshape(x.shape[0], x.shape[1], x.shape[2] * x.shape[3])

        wx = self.w(x)

        return wx
