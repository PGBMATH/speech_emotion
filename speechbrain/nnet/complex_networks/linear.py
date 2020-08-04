"""Library implementing complex-valued linear transformation.

Authors
 * Titouan Parcollet 2020
"""

import torch
import logging
from speechbrain.nnet.complex_networks.complex_ops import (
    complex_linear,
    check_complex_input,
)

logger = logging.getLogger(__name__)


class ComplexLinear(torch.nn.Module):
    """ This function implements a fully connected complex-valued
        linear layer: y = Wx + b. y, W, x and b are thus complex
        numbers. A complex number is written as: r + xi. A tensor of
        complex numbers x = [batch, 32] can be understood as
        [batch, 0:15] = R and [batch, 16:31] = Xi. Thus the features
        dimension is cut in half (must be dividible by 2).

    Arguments
    ---------
    n_neurons : int
          it is the number of output neurons (i.e, the dimensionality of the
          output). Please note that these are complex-valued neurons. If 256
          neurons are specified, the output dimension will be 512.
    bias : bool
        if True, the additive bias b is adopted.
    init_criterion: str , optional
        Default: he.
        (glorot, he).
        This parameter controls the initialization criterion of the weights.
        It is combined with weights_init to build the initialization method of
        the complex-valued weights.
    weight_init: str, optional
        Default: complex.
        (complex, unitary).
        This parameter defines the initialization procedure of the
        complex-valued weights. "complex" will generate random complex-valued
        weights following the init_criterion and the complex polar form.
        "unitary" will normalize the weights to lie on the unit circle.
        More details in: "Deep Complex Networks", Trabelsi C. et al.

    Example
    -------
    >>> lin = ComplexLinear(n_neurons=100)
    >>> inputs = torch.rand(10, 50, 40)
    >>> output = lin(inputs, init_params=True)
    >>> output.shape
    torch.Size([10, 50, 200])
    """

    def __init__(
        self,
        n_neurons,
        bias=True,
        init_criterion="glorot",
        weight_init="complex",
    ):
        super().__init__()
        self.n_neurons = n_neurons
        self.bias = bias
        self.init_criterion = init_criterion
        self.weight_init = weight_init

    def init_params(self, first_input):
        """
        Arguments
        ---------
        first_input : tensor
            A first input used for initializing the parameters.
        """

        # Check the complex_valued form of the input
        check_complex_input(first_input[0])

        # Computing the complex dimensionality of the input
        fea_dim = first_input[0].shape[-1] // 2

        self.in_features = fea_dim
        self.out_features = self.n_neurons

        self.linear = complex_linear(
            self.in_features,
            self.out_features,
            self.bias,
            self.init_criterion,
            self.weight_init,
            first_input.device,
        )

    def forward(self, x, init_params=False):
        """Returns the linear transformation of input tensor.

        Arguments
        ---------
        x : torch.Tensor
            input to transform linearly.
        """
        if init_params:
            self.init_params(x)

        wx = self.linear(x)

        return wx
