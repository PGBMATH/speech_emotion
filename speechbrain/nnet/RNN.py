"""Library implementing recurrent neural networks.

Authors
 * Mirco Ravanelli 2020
 * Ju-Chieh Chou 2020
 * Jianyuan Zhong 2020
 * Loren Lugosch 2020
"""

import torch
import logging
import torch.nn as nn
from speechbrain.nnet.attention import (
    ContentBasedAttention,
    LocationAwareAttention,
    KeyValueAttention,
)
from torch import Tensor
from typing import Optional

logger = logging.getLogger(__name__)


def pack_padded_sequence(inputs, lengths):
    """Returns packed speechbrain-formatted tensors.

    Arguments
    ---------
    inputs : torch.Tensor
        The sequences to pack.
    lengths : torch.Tensor
        The length of each sequence.
    """
    return torch.nn.utils.rnn.pack_padded_sequence(
        inputs, lengths, batch_first=True, enforce_sorted=False
    )


def pad_packed_sequence(inputs, max_length):
    """Returns speechbrain-formatted tensor from packed sequences.

    Arguments
    ---------
    inputs : torch.nn.utils.rnn.PackedSequence
        An input set of sequences to convert to a tensor.
    max_length: torch.Tensor
        The max sequence length of the batch, must be specified to support nn.DataParallel.
    """
    outputs, lengths = torch.nn.utils.rnn.pad_packed_sequence(
        inputs, total_length=max_length, batch_first=True
    )
    return outputs


class RNN(torch.nn.Module):
    """This function implements a vanilla RNN.

    It accepts in input tensors formatted as (batch, time, fea).
    In the case of 4d inputs like (batch, time, fea, channel) the tensor is
    flattened as (batch, time, fea*channel).

    Arguments
    ---------
    hidden_size : int
        Number of output neurons (i.e, the dimensionality of the output).
        values (i.e, time and frequency kernel sizes respectively).
    input_shape : tuple
        The shape of an example input. Alternatively, use ``input_size``.
    input_size : int
        The size of the input. Alternatively, use ``input_shape``.
    nonlinearity : str
        Type of nonlinearity (tanh, relu).
    num_layers : int
        Number of layers to employ in the RNN architecture.
    bias : bool
        If True, the additive bias b is adopted.
    dropout : float
        It is the dropout factor (must be between 0 and 1).
    re_init : bool
        If True, orthogonal initialization is used for the recurrent weights.
        Xavier initialization is used for the input connection weights.
    bidirectional : bool
        If True, a bidirectional model that scans the sequence both
        right-to-left and left-to-right is used.

    Example
    -------
    >>> inp_tensor = torch.rand([4, 10, 20])
    >>> net = RNN(hidden_size=5, input_shape=inp_tensor.shape)
    >>> out_tensor, _ = net(inp_tensor)
    >>>
    torch.Size([4, 10, 5])
    """

    def __init__(
        self,
        hidden_size,
        input_shape=None,
        input_size=None,
        nonlinearity="relu",
        num_layers=1,
        bias=True,
        dropout=0.0,
        re_init=True,
        bidirectional=False,
    ):
        super().__init__()
        self.reshape = False

        if input_shape is None and input_size is None:
            raise ValueError("Expected one of input_shape or input_size.")

        # Computing the feature dimensionality
        if input_size is None:
            if len(input_shape) > 3:
                self.reshape = True
            input_size = torch.prod(torch.tensor(input_shape[2:]))

        self.rnn = torch.nn.RNN(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            dropout=dropout,
            bidirectional=bidirectional,
            bias=bias,
            batch_first=True,
            nonlinearity=nonlinearity,
        )

        if re_init:
            rnn_init(self.rnn)

    def forward(self, x, hx=None, mask=None):
        """Returns the output of the vanilla RNN.

        Arguments
        ---------
        x : torch.Tensor
            Input tensor.
        hx : torch.Tensor
            Starting hidden state.
        lengths : torch.Tensor
            Relative lengths of the input signals.
        """
        # Reshaping input tensors for 4d inputs
        if self.reshape:
            if x.ndim == 4:
                x = x.reshape(x.shape[0], x.shape[1], x.shape[2] * x.shape[3])

        # Flatten params for data parallel
        self.rnn.flatten_parameters()

        # Pack sequence for proper RNN handling of padding
        if mask is not None:
            if mask.ndim == 4:
                mask = mask[:, :, 0]
            lengths = torch.sum(~mask, dim=1).squeeze(-1).cpu()
            max_length = x.size(1)
            x = pack_padded_sequence(x, lengths)

        # Support custom initial state
        if hx is not None:
            output, hn = self.rnn(x, hx=hx)
        else:
            output, hn = self.rnn(x)

        # Unpack the packed sequence
        if mask is not None:
            output = pad_packed_sequence(output, max_length)

        return (output, hn), mask


class LSTM(torch.nn.Module):
    """This function implements a basic LSTM.

    It accepts in input tensors formatted as (batch, time, fea).
    In the case of 4d inputs like (batch, time, fea, channel) the tensor is
    flattened as (batch, time, fea*channel).

    Arguments
    ---------
    hidden_size : int
        Number of output neurons (i.e, the dimensionality of the output).
        values (i.e, time and frequency kernel sizes respectively).
    input_shape : tuple
        The shape of an example input. Alternatively, use ``input_size``.
    input_size : int
        The size of the input. Alternatively, use ``input_shape``.
    num_layers : int
        Number of layers to employ in the RNN architecture.
    bias : bool
        If True, the additive bias b is adopted.
    dropout : float
        It is the dropout factor (must be between 0 and 1).
    re_init : bool
        It True, orthogonal initialization is used for the recurrent weights.
        Xavier initialization is used for the input connection weights.
    bidirectional : bool
        If True, a bidirectional model that scans the sequence both
        right-to-left and left-to-right is used.

    Example
    -------
    >>> inp_tensor = torch.rand([4, 10, 20])
    >>> net = LSTM(hidden_size=5, input_shape=inp_tensor.shape)
    >>> out_tensor = net(inp_tensor)
    >>>
    torch.Size([4, 10, 5])
    """

    def __init__(
        self,
        hidden_size,
        input_shape=None,
        input_size=None,
        num_layers=1,
        bias=True,
        dropout=0.0,
        re_init=True,
        bidirectional=False,
    ):
        super().__init__()
        self.reshape = False

        if input_shape is None and input_size is None:
            raise ValueError("Expected one of input_shape or input_size.")

        # Computing the feature dimensionality
        if input_size is None:
            if len(input_shape) > 3:
                self.reshape = True
            input_size = torch.prod(torch.tensor(input_shape[2:])).item()

        self.rnn = torch.nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            dropout=dropout,
            bidirectional=bidirectional,
            bias=bias,
            batch_first=True,
        )

        if re_init:
            rnn_init(self.rnn)

    def forward(self, x, hx=None, mask=None):
        """Returns the output of the LSTM.

        Arguments
        ---------
        x : torch.Tensor
            Input tensor.
        hx : torch.Tensor
            Starting hidden state.
        lengths : torch.Tensor
            Relative length of the input signals.
        """
        # Reshaping input tensors for 4d inputs
        if self.reshape:
            if x.ndim == 4:
                x = x.reshape(x.shape[0], x.shape[1], x.shape[2] * x.shape[3])

        # Flatten params for data parallel
        self.rnn.flatten_parameters()

        # Pack sequence for proper RNN handling of padding
        if mask is not None:
            if mask.ndim == 4:
                mask = mask[:, :, 0]
            lengths = torch.sum(~mask, dim=1).squeeze(-1).cpu()
            max_length = x.size(1)
            x = pack_padded_sequence(x, lengths)

        # Support custom initial state
        if hx is not None:
            output, hn = self.rnn(x, hx=hx)
        else:
            output, hn = self.rnn(x)

        # Unpack the packed sequence
        if mask is not None:
            output = pad_packed_sequence(output, max_length)

        return (output, hn), mask


class GRU(torch.nn.Module):
    """ This function implements a basic GRU.

    It accepts input tensors formatted as (batch, time, fea).
    In the case of 4d inputs like (batch, time, fea, channel) the tensor is
    flattened as (batch, time, fea*channel).

    Arguments
    ---------
    hidden_size : int
        Number of output neurons (i.e, the dimensionality of the output).
        values (i.e, time and frequency kernel sizes respectively).
    input_shape : tuple
        The shape of an example input. Alternatively, use ``input_size``.
    input_size : int
        The size of the input. Alternatively, use ``input_shape``.
    num_layers : int
        Number of layers to employ in the RNN architecture.
    bias : bool
        If True, the additive bias b is adopted.
    dropou t: float
        It is the dropout factor (must be between 0 and 1).
    re_init : bool
        If True, orthogonal initialization is used for the recurrent weights.
        Xavier initialization is used for the input connection weights.
    bidirectional : bool
        If True, a bidirectional model that scans the sequence both
        right-to-left and left-to-right is used.

    Example
    -------
    >>> inp_tensor = torch.rand([4, 10, 20])
    >>> net = GRU(hidden_size=5, input_shape=inp_tensor.shape)
    >>> out_tensor, _ = net(inp_tensor)
    >>>
    torch.Size([4, 10, 5])
    """

    def __init__(
        self,
        hidden_size,
        input_shape=None,
        input_size=None,
        num_layers=1,
        bias=True,
        dropout=0.0,
        re_init=True,
        bidirectional=False,
    ):
        super().__init__()
        self.reshape = False

        if input_shape is None and input_size is None:
            raise ValueError("Expected one of input_shape or input_size.")

        # Computing the feature dimensionality
        if input_size is None:
            if len(input_shape) > 3:
                self.reshape = True
            input_size = torch.prod(torch.tensor(input_shape[2:])).item()

        self.rnn = torch.nn.GRU(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            dropout=dropout,
            bidirectional=bidirectional,
            bias=bias,
            batch_first=True,
        )

        if re_init:
            rnn_init(self.rnn)

    def forward(self, x, hx=None, mask=None):
        """Returns the output of the GRU.

        Arguments
        ---------
        x : torch.Tensor
            Input tensor.
        hx : torch.Tensor
            Starting hidden state.
        lengths : torch.Tensor
            Relative length of the input signals.
        """
        # Reshaping input tensors for 4d inputs
        if self.reshape:
            if x.ndim == 4:
                x = x.reshape(x.shape[0], x.shape[1], x.shape[2] * x.shape[3])

        # Flatten params for data parallel
        self.rnn.flatten_parameters()

        # Pack sequence for proper RNN handling of padding
        if mask is not None:
            if mask.ndim == 4:
                mask = mask[:, :, 0]
            lengths = torch.sum(~mask, dim=1).squeeze(-1).cpu()
            max_length = x.size(1)
            x = pack_padded_sequence(x, lengths)

        # Support custom initial state
        if hx is not None:
            output, hn = self.rnn(x, hx=hx)
        else:
            output, hn = self.rnn(x)

        # Unpack the packed sequence
        if mask is not None:
            output = pad_packed_sequence(output, max_length)

        return (output, hn), mask


class RNNCell(nn.Module):
    """ This class implements a basic RNN Cell for a timestep of input,
    while RNN() takes the whole sequence as input.

    It is designed for an autoregressive decoder (ex. attentional decoder),
    which takes one input at a time.
    Using torch.nn.RNNCell() instead of torch.nn.RNN() to reduce VRAM
    consumption.

    It accepts in input tensors formatted as (batch, fea).

    Arguments
    ---------
    hidden_size : int
        Number of output neurons (i.e, the dimensionality of the output).
    input_shape : tuple
        The shape of an example input. Alternatively, use ``input_size``.
    input_size : int
        The size of the input. Alternatively, use ``input_shape``.
    num_layers : int
        Number of layers to employ in the RNN architecture.
    bias : bool
        If True, the additive bias b is adopted.
    dropout : float
        It is the dropout factor (must be between 0 and 1).
    re_init : bool
        It True, orthogonal initialization is used for the recurrent weights.
        Xavier initialization is used for the input connection weights.

    Example
    -------
    >>> inp_tensor = torch.rand([4, 20])
    >>> net = RNNCell(hidden_size=5, input_shape=inp_tensor.shape)
    >>> out_tensor, _ = net(inp_tensor)
    >>> out_tensor.shape
    torch.Size([4, 5])
    """

    def __init__(
        self,
        hidden_size,
        input_shape=None,
        input_size=None,
        num_layers=1,
        bias=True,
        dropout=0.0,
        re_init=True,
        nonlinearity="tanh",
    ):
        super(RNNCell, self).__init__()
        self.hidden_size = hidden_size
        self.num_layers = num_layers

        if input_shape is None and input_size is None:
            raise ValueError("Expected one of input_shape or input_size.")

        # Computing the feature dimensionality
        if input_size is None:
            if len(input_shape) > 3:
                self.reshape = True
            input_size = torch.prod(torch.tensor(input_shape[1:]))

        kwargs = {
            "input_size": input_size,
            "hidden_size": self.hidden_size,
            "bias": bias,
            "nonlinearity": nonlinearity,
        }

        self.rnn_cells = nn.ModuleList([torch.nn.RNNCell(**kwargs)])
        kwargs["input_size"] = self.hidden_size

        for i in range(self.num_layers - 1):
            self.rnn_cells.append(torch.nn.RNNCell(**kwargs))

        self.dropout_layers = nn.ModuleList(
            [torch.nn.Dropout(p=dropout) for _ in range(self.num_layers - 1)]
        )

        if re_init:
            rnn_init(self.rnn_cells)

    def forward(self, x, hx=None):
        """Returns the output of the RNNCell.

        Arguments
        ---------
        x : torch.Tensor
            The input of RNNCell.
        hx : torch.Tensor
            The hidden states of RNNCell.
        """
        # if not provided, initialized with zeros
        if hx is None:
            hx = x.new_zeros(self.num_layers, x.shape[0], self.hidden_size)

        h = self.rnn_cells[0](x, hx[0])
        hidden_lst = [h]
        for i in range(1, self.num_layers):
            drop_h = self.dropout_layers[i - 1](h)
            h = self.rnn_cells[i](drop_h, hx[i])
            hidden_lst.append(h)

        hidden = torch.stack(hidden_lst, dim=0)
        return h, hidden


class GRUCell(nn.Module):
    """ This class implements a basic GRU Cell for a timestep of input,
    while GRU() takes the whole sequence as input.

    It is designed for an autoregressive decoder (ex. attentional decoder),
    which takes one input at a time.
    Using torch.nn.GRUCell() instead of torch.nn.GRU() to reduce VRAM
    consumption.
    It accepts in input tensors formatted as (batch, fea).

    Arguments
    ---------
    hidden_size: int
        Number of output neurons (i.e, the dimensionality of the output).
    input_shape : tuple
        The shape of an example input. Alternatively, use ``input_size``.
    input_size : int
        The size of the input. Alternatively, use ``input_shape``.
    num_layers : int
        Number of layers to employ in the GRU architecture.
    bias : bool
        If True, the additive bias b is adopted.
    dropout : float
        It is the dropout factor (must be between 0 and 1).
    re_init : bool
        It True, orthogonal initialization is used for the recurrent weights.
        Xavier initialization is used for the input connection weights.

    Example
    -------
    >>> inp_tensor = torch.rand([4, 20])
    >>> net = GRUCell(hidden_size=5, input_shape=inp_tensor.shape)
    >>> out_tensor, _ = net(inp_tensor)
    >>> out_tensor.shape
    torch.Size([4, 5])
    """

    def __init__(
        self,
        hidden_size,
        input_shape=None,
        input_size=None,
        num_layers=1,
        bias=True,
        dropout=0.0,
        re_init=True,
    ):
        super(GRUCell, self).__init__()
        self.hidden_size = hidden_size
        self.num_layers = num_layers

        if input_shape is None and input_size is None:
            raise ValueError("Expected one of input_shape or input_size.")

        # Computing the feature dimensionality
        if input_size is None:
            if len(input_shape) > 3:
                self.reshape = True
            input_size = torch.prod(torch.tensor(input_shape[1:]))

        kwargs = {
            "input_size": input_size,
            "hidden_size": self.hidden_size,
            "bias": bias,
        }

        self.rnn_cells = nn.ModuleList([torch.nn.GRUCell(**kwargs)])
        kwargs["input_size"] = self.hidden_size

        for i in range(self.num_layers - 1):
            self.rnn_cells.append(torch.nn.GRUCell(**kwargs))

        self.dropout_layers = nn.ModuleList(
            [torch.nn.Dropout(p=dropout) for _ in range(self.num_layers - 1)]
        )

        if re_init:
            rnn_init(self.rnn_cells)

    def forward(self, x, hx=None):
        """Returns the output of the GRUCell.

        Arguments
        ---------
        x : torch.Tensor
            The input of GRUCell.
        hx : torch.Tensor
            The hidden states of GRUCell.
        """

        # if not provided, initialized with zeros
        if hx is None:
            hx = x.new_zeros(self.num_layers, x.shape[0], self.hidden_size)

        h = self.rnn_cells[0](x, hx[0])
        hidden_lst = [h]
        for i in range(1, self.num_layers):
            drop_h = self.dropout_layers[i - 1](h)
            h = self.rnn_cells[i](drop_h, hx[i])
            hidden_lst.append(h)

        hidden = torch.stack(hidden_lst, dim=0)
        return h, hidden


class LSTMCell(nn.Module):
    """ This class implements a basic LSTM Cell for a timestep of input,
    while LSTM() takes the whole sequence as input.

    It is designed for an autoregressive decoder (ex. attentional decoder),
    which takes one input at a time.
    Using torch.nn.LSTMCell() instead of torch.nn.LSTM() to reduce VRAM
    consumption.
    It accepts in input tensors formatted as (batch, fea).

    Arguments
    ---------
    hidden_size: int
        Number of output neurons (i.e, the dimensionality of the output).
    input_shape : tuple
        The shape of an example input. Alternatively, use ``input_size``.
    input_size : int
        The size of the input. Alternatively, use ``input_shape``.
    num_layers : int
        Number of layers to employ in the LSTM architecture.
    bias : bool
        If True, the additive bias b is adopted.
    dropout : float
        It is the dropout factor (must be between 0 and 1).
    re_init : bool
        If True, orthogonal initialization is used for the recurrent weights.
        Xavier initialization is used for the input connection weights.

    Example
    -------
    >>> inp_tensor = torch.rand([4, 20])
    >>> net = LSTMCell(hidden_size=5, input_shape=inp_tensor.shape)
    >>> out_tensor, _ = net(inp_tensor)
    >>> out_tensor.shape
    torch.Size([4, 5])
    """

    def __init__(
        self,
        hidden_size,
        input_shape=None,
        input_size=None,
        num_layers=1,
        bias=True,
        dropout=0.0,
        re_init=True,
    ):
        super(LSTMCell, self).__init__()
        self.hidden_size = hidden_size
        self.num_layers = num_layers

        if input_shape is None and input_size is None:
            raise ValueError("Expected one of input_shape or input_size.")

        # Computing the feature dimensionality
        if input_size is None:
            if len(input_shape) > 3:
                self.reshape = True
            input_size = torch.prod(torch.tensor(input_shape[1:]))

        kwargs = {
            "input_size": input_size,
            "hidden_size": self.hidden_size,
            "bias": bias,
        }

        self.rnn_cells = nn.ModuleList([torch.nn.LSTMCell(**kwargs)])
        kwargs["input_size"] = self.hidden_size

        for i in range(self.num_layers - 1):
            self.rnn_cells.append(torch.nn.LSTMCell(**kwargs))

        self.dropout_layers = nn.ModuleList(
            [torch.nn.Dropout(p=dropout) for _ in range(self.num_layers - 1)]
        )

        if re_init:
            rnn_init(self.rnn_cells)

    def forward(self, x, hx=None):
        """Returns the output of the LSTMCell.

        Arguments
        ---------
        x : torch.Tensor
            The input of LSTMCell.
        hx : torch.Tensor
            The hidden states of LSTMCell.
        """
        # if not provided, initialized with zeros
        if hx is None:
            hx = (
                x.new_zeros(self.num_layers, x.shape[0], self.hidden_size),
                x.new_zeros(self.num_layers, x.shape[0], self.hidden_size),
            )

        h, c = self.rnn_cells[0](x, (hx[0][0], hx[1][0]))
        hidden_lst = [h]
        cell_lst = [c]
        for i in range(1, self.num_layers):
            drop_h = self.dropout_layers[i - 1](h)
            h, c = self.rnn_cells[i](drop_h, (hx[0][i], hx[1][i]))
            hidden_lst.append(h)
            cell_lst.append(c)

        hidden = torch.stack(hidden_lst, dim=0)
        cell = torch.stack(cell_lst, dim=0)
        return h, (hidden, cell)


class AttentionalRNNDecoder(nn.Module):
    """This function implements RNN decoder model with attention.

    This function implements different RNN models. It accepts in enc_states
    tensors formatted as (batch, time, fea). In the case of 4d inputs
    like (batch, time, fea, channel) the tensor is flattened in this way:
    (batch, time, fea*channel).

    Arguments
    ---------
    rnn_type : str
        Type of recurrent neural network to use (rnn, lstm, gru).
    attn_type : str
        type of attention to use (location, content).
    hidden_size : int
        Number of the neurons.
    attn_dim : int
        Number of attention module internal and output neurons.
    num_layers : int
        Number of layers to employ in the RNN architecture.
    input_shape : tuple
        Expected shape of an input.
    input_size : int
        Expected size of the relevant input dimension.
    nonlinearity : str
        Type of nonlinearity (tanh, relu). This option is active for
        rnn and ligru models only. For lstm and gru tanh is used.
    re_init : bool
        It True, orthogonal init is used for the recurrent weights.
        Xavier initialization is used for the input connection weights.
    normalization : str
        Type of normalization for the ligru model (batchnorm, layernorm).
        Every string different from batchnorm and layernorm will result
        in no normalization.
    scaling : float
        A scaling factor to sharpen or smoothen the attention distribution.
    channels : int
        Number of channels for location-aware attention.
    kernel_size : int
        Size of the kernel for location-aware attention.
    bias : bool
        If True, the additive bias b is adopted.
    dropout : float
        It is the dropout factor (must be between 0 and 1).

    Example
    -------
    >>> enc_states = torch.rand([4, 10, 20])
    >>> wav_len = torch.rand([4])
    >>> inp_tensor = torch.rand([4, 5, 6])
    >>> net = AttentionalRNNDecoder(
    ...     rnn_type="lstm",
    ...     attn_type="content",
    ...     hidden_size=7,
    ...     attn_dim=5,
    ...     num_layers=1,
    ...     enc_dim=20,
    ...     input_size=6,
    ... )
    >>> out_tensor, attn = net(inp_tensor, enc_states, wav_len)
    >>> out_tensor.shape
    torch.Size([4, 5, 7])
    """

    def __init__(
        self,
        rnn_type,
        attn_type,
        hidden_size,
        attn_dim,
        num_layers,
        enc_dim,
        input_size,
        nonlinearity="relu",
        re_init=True,
        normalization="batchnorm",
        scaling=1.0,
        channels=None,
        kernel_size=None,
        bias=True,
        dropout=0.0,
    ):
        super(AttentionalRNNDecoder, self).__init__()

        self.rnn_type = rnn_type.lower()
        self.attn_type = attn_type.lower()
        self.hidden_size = hidden_size
        self.attn_dim = attn_dim
        self.num_layers = num_layers
        self.scaling = scaling
        self.bias = bias
        self.dropout = dropout
        self.normalization = normalization
        self.re_init = re_init
        self.nonlinearity = nonlinearity

        # only for location-aware attention
        self.channels = channels
        self.kernel_size = kernel_size

        # Combining the context vector and output of rnn
        self.proj = nn.Linear(
            self.hidden_size + self.attn_dim, self.hidden_size
        )

        if self.attn_type == "content":
            self.attn = ContentBasedAttention(
                enc_dim=enc_dim,
                dec_dim=self.hidden_size,
                attn_dim=self.attn_dim,
                output_dim=self.attn_dim,
                scaling=self.scaling,
            )

        elif self.attn_type == "location":
            self.attn = LocationAwareAttention(
                enc_dim=enc_dim,
                dec_dim=self.hidden_size,
                attn_dim=self.attn_dim,
                output_dim=self.attn_dim,
                conv_channels=self.channels,
                kernel_size=self.kernel_size,
                scaling=self.scaling,
            )

        elif self.attn_type == "keyvalue":
            self.attn = KeyValueAttention(
                enc_dim=enc_dim,
                dec_dim=self.hidden_size,
                attn_dim=self.attn_dim,
                output_dim=self.attn_dim,
            )

        else:
            raise ValueError(f"{self.attn_type} is not implemented.")

        self.drop = nn.Dropout(p=self.dropout)

        # set dropout to 0 when only one layer
        dropout = 0 if self.num_layers == 1 else self.dropout

        # using cell implementation to reduce the usage of memory
        if self.rnn_type == "rnn":
            cell_class = RNNCell
        elif self.rnn_type == "gru":
            cell_class = GRUCell
        elif self.rnn_type == "lstm":
            cell_class = LSTMCell
        else:
            raise ValueError(f"{self.rnn_type} not implemented.")

        kwargs = {
            "input_size": input_size + self.attn_dim,
            "hidden_size": self.hidden_size,
            "num_layers": self.num_layers,
            "bias": self.bias,
            "dropout": dropout,
            "re_init": self.re_init,
        }
        if self.rnn_type == "rnn":
            kwargs["nonlinearity"] = self.nonlinearity

        self.rnn = cell_class(**kwargs)

    def forward_step(self, inp, hs, c, enc_states, enc_len):
        """One step of forward pass process.

        Arguments
        ---------
        inp : torch.Tensor
            The input of current timestep.
        hs : torch.Tensor or tuple of torch.Tensor
            The cell state for RNN.
        c : torch.Tensor
            The context vector of previous timestep.
        enc_states : torch.Tensor
            The tensor generated by encoder, to be attended.
        enc_len : torch.LongTensor
            The actual length of encoder states.

        Returns
        -------
        dec_out : torch.Tensor
            The output tensor.
        hs : torch.Tensor or tuple of torch.Tensor
            The new cell state for RNN.
        c : torch.Tensor
            The context vector of the current timestep.
        w : torch.Tensor
            The weight of attention.
        """
        cell_inp = torch.cat([inp, c], dim=-1)
        cell_inp = self.drop(cell_inp)
        cell_out, hs = self.rnn(cell_inp, hs)

        c, w = self.attn(enc_states, enc_len, cell_out)
        dec_out = torch.cat([c, cell_out], dim=1)
        dec_out = self.proj(dec_out)

        return dec_out, hs, c, w

    def forward(self, inp_tensor, enc_states, wav_len):
        """This method implements the forward pass of the attentional RNN decoder.

        Arguments
        ---------
        inp_tensor : torch.Tensor
            The input tensor for each timesteps of RNN decoder.
        enc_states : torch.Tensor
            The tensor to be attended by the decoder.
        wav_len : torch.Tensor
            This variable stores the relative length of wavform.

        Returns
        -------
        outputs : torch.Tensor
            The output of the RNN decoder.
        attn : torch.Tensor
            The attention weight of each timestep.
        """
        # calculating the actual length of enc_states
        enc_len = torch.round(enc_states.shape[1] * wav_len).long()

        # initialization
        self.attn.reset()
        c = torch.zeros(
            enc_states.shape[0], self.attn_dim, device=enc_states.device
        )
        hs = None

        # store predicted tokens
        outputs_lst, attn_lst = [], []
        for t in range(inp_tensor.shape[1]):
            outputs, hs, c, w = self.forward_step(
                inp_tensor[:, t], hs, c, enc_states, enc_len
            )
            outputs_lst.append(outputs)
            attn_lst.append(w)

        # [B, L_d, hidden_size]
        outputs = torch.stack(outputs_lst, dim=1)

        # [B, L_d, L_e]
        attn = torch.stack(attn_lst, dim=1)

        return outputs, attn


class LiGRU(torch.nn.Module):
    """ This function implements a Light GRU (liGRU).

    LiGRU is single-gate GRU model based on batch-norm + relu
    activations + recurrent dropout. For more info see:

    "M. Ravanelli, P. Brakel, M. Omologo, Y. Bengio,
    Light Gated Recurrent Units for Speech Recognition,
    in IEEE Transactions on Emerging Topics in Computational Intelligence,
    2018" (https://arxiv.org/abs/1803.10225)

    This is a custm RNN and to speed it up it must be compiled with
    the torch just-in-time compiler (jit) right before using it.
    You can compile it with:
    compiled_model = torch.jit.script(model)

    It accepts in input tensors formatted as (batch, time, fea).
    In the case of 4d inputs like (batch, time, fea, channel) the tensor is
    flattened as (batch, time, fea*channel).

    Arguments
    ---------
    hidden_size : int
        Number of output neurons (i.e, the dimensionality of the output).
        values (i.e, time and frequency kernel sizes respectively).
    input_shape : tuple
        The shape of an example input.
    nonlinearity : str
        Type of nonlinearity (tanh, relu).
    normalization : str
        Type of normalization for the ligru model (batchnorm, layernorm).
        Every string different from batchnorm and layernorm will result
        in no normalization.
    num_layers : int
        Number of layers to employ in the RNN architecture.
    bias : bool
        If True, the additive bias b is adopted.
    dropout : float
        It is the dropout factor (must be between 0 and 1).
    re_init : bool
        If True, orthogonal initialization is used for the recurrent weights.
        Xavier initialization is used for the input connection weights.
    bidirectional : bool
        If True, a bidirectional model that scans the sequence both
        right-to-left and left-to-right is used.

    Example
    -------
    >>> inp_tensor = torch.rand([4, 10, 20])
    >>> net = LiGRU(input_shape=inp_tensor.shape, hidden_size=5)
    >>> out_tensor, _ = net(inp_tensor)
    >>>
    torch.Size([4, 10, 5])
    """

    def __init__(
        self,
        hidden_size,
        input_shape,
        nonlinearity="relu",
        normalization="batchnorm",
        num_layers=1,
        bias=True,
        dropout=0.0,
        re_init=True,
        bidirectional=False,
    ):
        super().__init__()
        self.hidden_size = hidden_size
        self.nonlinearity = nonlinearity
        self.num_layers = num_layers
        self.normalization = normalization
        self.bias = bias
        self.dropout = dropout
        self.re_init = re_init
        self.bidirectional = bidirectional
        self.reshape = False

        # Computing the feature dimensionality
        if len(input_shape) > 3:
            self.reshape = True
        self.fea_dim = float(torch.prod(torch.tensor(input_shape[2:])))
        self.batch_size = input_shape[0]
        self.rnn = self._init_layers()

        if self.re_init:
            rnn_init(self.rnn)

    def _init_layers(self):
        """Initializes the layers of the liGRU."""
        rnn = torch.nn.ModuleList([])
        current_dim = self.fea_dim

        for i in range(self.num_layers):
            rnn_lay = LiGRU_Layer(
                current_dim,
                self.hidden_size,
                self.num_layers,
                self.batch_size,
                dropout=self.dropout,
                nonlinearity=self.nonlinearity,
                normalization=self.normalization,
                bidirectional=self.bidirectional,
            )
            rnn.append(rnn_lay)

            if self.bidirectional:
                current_dim = self.hidden_size * 2
            else:
                current_dim = self.hidden_size
        return rnn

    def forward(self, x, hx: Optional[Tensor] = None, mask=None):
        """Returns the output of the liGRU.

        Arguments
        ---------
        x : torch.Tensor
            The input tensor.
        hx : torch.Tensor
            Starting hidden state.
        """
        # Reshaping input tensors for 4d inputs
        if self.reshape:
            if x.ndim == 4:
                x = x.reshape(x.shape[0], x.shape[1], x.shape[2] * x.shape[3])
        if mask is not None and mask.ndim == 4:
            mask = mask[:, :, 0]

        # run ligru
        output, hh, mask = self._forward_ligru(x, hx=hx, mask=mask)

        if mask is not None:
            output.masked_fill_(mask, 0.0)

        return (output, hh), mask

    def _forward_ligru(self, x, hx: Optional[Tensor], mask=None):
        """Returns the output of the vanilla liGRU.

        Arguments
        ---------
        x : torch.Tensor
            Input tensor.
        hx : torch.Tensor
        """
        h = []
        if hx is not None:
            if self.bidirectional:
                hx = hx.reshape(
                    self.num_layers, self.batch_size * 2, self.hidden_size
                )
        # Processing the different layers
        for i, ligru_lay in enumerate(self.rnn):
            if hx is not None:
                x, mask = ligru_lay(x, hx=hx[i], mask=mask)
            else:
                x, mask = ligru_lay(x, hx=None, mask=mask)
            h.append(x[:, -1, :])
        h = torch.stack(h, dim=1)

        if self.bidirectional:
            h = h.reshape(h.shape[1] * 2, h.shape[0], self.hidden_size)
        else:
            h = h.transpose(0, 1)

        return x, h, mask


class LiGRU_Layer(torch.nn.Module):
    """ This function implements Light-Gated Recurrent Units (ligru) layer.

    Arguments
    ---------
    input_size : int
        Feature dimensionality of the input tensors.
    batch_size : int
        Batch size of the input tensors.
    hidden_size : int
        Number of output neurons.
    num_layers : int
        Number of layers to employ in the RNN architecture.
    nonlinearity : str
        Type of nonlinearity (tanh, relu).
    normalization : str
        Type of normalization (batchnorm, layernorm).
        Every string different from batchnorm and layernorm will result
        in no normalization.
    dropout : float
        It is the dropout factor (must be between 0 and 1).
    bidirectional : bool
        if True, a bidirectional model that scans the sequence both
        right-to-left and left-to-right is used.
    """

    def __init__(
        self,
        input_size,
        hidden_size,
        num_layers,
        batch_size,
        dropout=0.0,
        nonlinearity="relu",
        normalization="batchnorm",
        bidirectional=False,
    ):

        super(LiGRU_Layer, self).__init__()
        self.hidden_size = int(hidden_size)
        self.input_size = int(input_size)
        self.batch_size = batch_size
        self.bidirectional = bidirectional
        self.dropout = dropout

        self.w = nn.Linear(self.input_size, 2 * self.hidden_size, bias=False)

        self.u = nn.Linear(self.hidden_size, 2 * self.hidden_size, bias=False)

        if self.bidirectional:
            self.batch_size = self.batch_size * 2

        # Initializing batch norm
        self.normalize = False

        if normalization == "batchnorm":
            self.norm = nn.BatchNorm1d(2 * self.hidden_size, momentum=0.05)
            self.normalize = True

        elif normalization == "layernorm":
            self.norm = torch.nn.LayerNorm(2 * self.hidden_size)
            self.normalize = True
        else:
            # Normalization is disabled here. self.norm is only  formally
            # initialized to avoid jit issues.
            self.norm = torch.nn.LayerNorm(2 * self.hidden_size)
            self.normalize = True

        # Initial state
        self.register_buffer("h_init", torch.zeros(1, self.hidden_size))

        # Preloading dropout masks (gives some speed improvement)
        self._init_drop(self.batch_size)

        # Setting the activation function
        if nonlinearity == "tanh":
            self.act = torch.nn.Tanh()
        elif nonlinearity == "sin":
            self.act = torch.sin
        elif nonlinearity == "leaky_relu":
            self.act = torch.nn.LeakyReLU()
        else:
            self.act = torch.nn.ReLU()

    def forward(self, x, hx: Optional[Tensor] = None, mask=None):
        # type: (Tensor, Optional[Tensor]) -> Tensor # noqa F821
        """Returns the output of the liGRU layer.

        Arguments
        ---------
        x : torch.Tensor
            Input tensor.
        """
        if self.bidirectional:
            if mask is None:
                x_flip = x.flip(1)
            else:
                lengths = torch.sum(~mask, dim=1).squeeze(-1)
                x_flip = reverse_padded_sequence(x, lengths, batch_first=True)
            x = torch.cat([x, x_flip], dim=0)

        # Change batch size if needed
        self._change_batch_size(x)

        # Feed-forward affine transformations (all steps in parallel)
        w = self.w(x)

        # Apply batch normalization
        if self.normalize:
            w_bn = self.norm(w.reshape(w.shape[0] * w.shape[1], w.shape[2]))
            w = w_bn.reshape(w.shape[0], w.shape[1], w.shape[2])

        # Processing time steps
        if hx is not None:
            h = self._ligru_cell(w, hx)
        else:
            h = self._ligru_cell(w, self.h_init)

        if self.bidirectional:
            h_f, h_b = h.chunk(2, dim=0)
            if mask is None:
                h_b = h_b.flip(1)
            else:
                h_b = reverse_padded_sequence(h_b, lengths, batch_first=True)
            h = torch.cat([h_f, h_b], dim=2)

        return h, mask

    def _ligru_cell(self, w, ht):
        """Returns the hidden states for each time step.

        Arguments
        ---------
        wx : torch.Tensor
            Linearly transformed input.
        """
        hiddens = []

        # Sampling dropout mask
        drop_mask = self._sample_drop_mask(w)

        # Loop over time axis
        for k in range(w.shape[1]):
            gates = w[:, k] + self.u(ht)
            at, zt = gates.chunk(2, 1)
            zt = torch.sigmoid(zt)
            hcand = self.act(at) * drop_mask
            ht = zt * ht + (1 - zt) * hcand
            hiddens.append(ht)

        # Stacking hidden states
        h = torch.stack(hiddens, dim=1)
        return h

    def _init_drop(self, batch_size):
        """Initializes the recurrent dropout operation. To speed it up,
        the dropout masks are sampled in advance.
        """
        self.drop = torch.nn.Dropout(p=self.dropout, inplace=False)
        self.N_drop_masks = 16000
        self.drop_mask_cnt = 0

        self.register_buffer(
            "drop_masks",
            self.drop(torch.ones(self.N_drop_masks, self.hidden_size)).data,
        )
        self.register_buffer("drop_mask_te", torch.tensor([1.0]).float())

    def _sample_drop_mask(self, w):
        """Selects one of the pre-defined dropout masks"""
        if self.training:

            # Sample new masks when needed
            if self.drop_mask_cnt + self.batch_size > self.N_drop_masks:
                self.drop_mask_cnt = 0
                self.drop_masks = self.drop(
                    torch.ones(
                        self.N_drop_masks, self.hidden_size, device=w.device
                    )
                ).data

            # Sampling the mask
            drop_mask = self.drop_masks[
                self.drop_mask_cnt : self.drop_mask_cnt + self.batch_size
            ]
            self.drop_mask_cnt = self.drop_mask_cnt + self.batch_size

        else:
            self.drop_mask_te = self.drop_mask_te.to(w.device)
            drop_mask = self.drop_mask_te

        return drop_mask

    def _change_batch_size(self, x):
        """This function changes the batch size when it is different from
        the one detected in the initialization method. This might happen in
        the case of multi-gpu or when we have different batch sizes in train
        and test. We also update the h_int and drop masks.
        """
        if self.batch_size != x.shape[0]:
            self.batch_size = x.shape[0]

            if self.training:
                self.drop_masks = self.drop(
                    torch.ones(
                        self.N_drop_masks, self.hidden_size, device=x.device,
                    )
                ).data


def rnn_init(module):
    """This function is used to initialize the RNN weight.
    Recurrent connection: orthogonal initialization.

    Arguments
    ---------
    module: torch.nn.Module
        Recurrent neural network module.

    Example
    -------
    >>> inp_tensor = torch.rand([4, 10, 20])
    >>> net = RNN(hidden_size=5, input_shape=inp_tensor.shape)
    >>> out_tensor = net(inp_tensor)
    >>> rnn_init(net)
    """
    for name, param in module.named_parameters():
        if "weight_hh" in name or ".u.weight" in name:
            nn.init.orthogonal_(param)


def reverse_padded_sequence(input, lengths, batch_first=False):
    """Reverses sequences according to their lengths.
    ``input`` should have size ``T x B x *`` if ``batch_first`` is False, or
    ``B x T x *`` if True. ``T`` is the length of the longest sequence (or
    larger), ``B`` is the batch size, and ``*`` is any number of dimensions
    (including 0). ``lengths`` must have size ``B`` and contain values between
    ``0`` and ``T``, inclusive.

    This function is borrowed from:
    https://github.com/pytorch/pytorch/commit/1b65ed072aa6908ee1266bd1e21275aa0b742a3e

    Arguments
    ---------
    input (Tensor): padded batch of variable length sequences.
    lengths (Tensor): list of sequence lengths.
    batch_first (bool, optional): if ``True``, input should be in
        ``B x T x *`` format.

    Returns:
    -------
    Tensor of size ``T x B x *`` if :attr:`batch_first` is ``False``.
    Tensor of size ``B x T x *`` otherwise

    Example:
    -------
    >>> input = torch.tensor([[1., 2.], [3., 4.], [5., 6.], [7., 8.]])
    >>> lengths = torch.tensor([3, 2])
    >>> reverse_padded_sequence(input, lengths)
    tensor([[5., 4.],
            [3., 2.],
            [1., 0.],
            [0., 0.]])
    >>> input = torch.tensor([[1., 2., 3., 4.], [5., 6., 7., 8.]])
    >>> lengths = torch.tensor([3, 2])
    >>> reverse_padded_sequence(input, lengths, batch_first=True)
    tensor([[3., 2., 1., 0.],
            [6., 5., 0., 0.]])
    """
    if batch_first:
        input = input.transpose(0, 1)

    max_length, batch_size, *trailing_dims = input.shape

    if len(lengths) != batch_size:
        raise ValueError(
            "lengths has batch size {} while input has batch "
            "size {}".format(len(lengths), batch_size)
        )

    # Compute the new (i.e. reversed) indices
    tiled_indices = torch.arange(max_length, device=lengths.device)
    tiled_indices = tiled_indices.view(
        (max_length, 1) + (1,) * len(trailing_dims)
    )
    tiled_indices = tiled_indices.expand_as(input)

    tiled_lengths = lengths.view((1, batch_size) + (1,) * len(trailing_dims))
    tiled_lengths = tiled_lengths.expand_as(input)

    new_tiled_indices = (tiled_lengths - tiled_indices - 1).clamp(min=0)

    # Reverse the sequences
    reversed_unmasked = input.gather(0, new_tiled_indices)

    # Any sequence whose length is less than `max_length` will have its last
    # element (which was previously the first) repeated in reversed_unmasked.
    # We set those to zero here.
    reversed_masked = reversed_unmasked * (tiled_indices < tiled_lengths)

    if batch_first:
        reversed_masked = reversed_masked.transpose(0, 1)

    return reversed_masked
