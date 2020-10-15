"""
Decoders and output normalization for CTC

Authors
 * Mirco Ravanelli 2020
 * Aku Rouhe 2020
 * Sung-Lin Yeh 2020
"""
import torch
import numpy as np
from itertools import groupby


from speechbrain.data_io.data_io import length_to_mask


class CTCPrefixScorer:
    def __init__(
        self, x, enc_lens, batch_size, beam_size, blank_index, eos_index
    ):
        self.blank_index = blank_index
        self.eos_index = eos_index
        self.max_enc_len = x.size(1)
        self.batch_size = batch_size
        self.beam_size = beam_size
        self.vocab_size = x.size(-1)
        self.device = x.device
        self.last_frame_index = enc_lens - 1

        # mask frames > enc_lens
        mask = 1 - length_to_mask(enc_lens)
        mask = mask.unsqueeze(-1).expand(-1, -1, x.size(-1)).eq(1)
        x.masked_fill_(mask, -np.inf)
        x[:, :, 0] = x[:, :, 0].masked_fill_(mask[:, :, 0], 0)

        # xnb: dim=0, nonblank posteriors, xb: dim=1, blank posteriors
        xnb = x.transpose(0, 1)
        xb = (
            xnb[:, :, self.blank_index]
            .unsqueeze(2)
            .expand(-1, -1, self.vocab_size)
        )

        # (2, L, batch_size * beam_size, vocab_size)
        self.x = torch.stack([xnb, xb])

        # The first index of each sentence.
        self.beam_offset = (torch.arange(batch_size) * self.beam_size).to(
            self.device
        )
        # The first index of each candidates.
        self.cand_offset = (torch.arange(batch_size) * self.vocab_size).to(
            self.device
        )

    def forward_step(self, g, state, candidates=None):
        """h = g + c
        candidates: (batch_size * beam_size, beam_size)
        """
        prefix_length = g.size(1)
        last_char = [gi[-1] for gi in g] if prefix_length > 0 else [0] * len(g)
        # TODO support scoring for candidates, candidates.size(-1)
        self.num_candidates = (
            self.vocab_size if candidates is None else candidates.size(-1)
        )

        if state is None:
            # r_prev: (max_enc_len, 2, batch_size * beam_size)
            r_prev = torch.Tensor(
                self.max_enc_len, 2, self.batch_size, self.beam_size
            ).to(self.device)
            r_prev.fill_(-np.inf)
            # Accumulate blank posteriors at each step
            r_prev[:, 1] = torch.cumsum(
                self.x[0, :, :, self.blank_index], 0
            ).unsqueeze(2)
            r_prev = r_prev.view(-1, 2, self.batch_size * self.beam_size)
            psi_prev = 0.0
        else:
            r_prev, psi_prev = state

        # for partial search
        if candidates is not None:
            scoring_table = (
                torch.Tensor(self.batch_size * self.beam_size, self.vocab_size)
                .to(self.device)
                .fill_(-1)
                .long()
            )
            # assign indices of candidates to their positions in the table
            col_index = torch.arange(
                self.batch_size * self.beam_size, device=self.device
            ).unsqueeze(1)
            scoring_table[col_index, candidates] = torch.arange(
                self.num_candidates,
            ).to(self.device)
            # select candidates indices for scoring
            scoring_index = (
                candidates
                + self.cand_offset.unsqueeze(1)
                .repeat(1, self.beam_size)
                .view(-1, 1)
            ).view(-1)
            x_inflate = torch.index_select(
                self.x.view(2, -1, self.batch_size * self.vocab_size),
                2,
                scoring_index,
            ).view(2, -1, self.batch_size * self.beam_size, self.num_candidates)
        # for full search
        else:
            scoring_table = None
            x_inflate = (
                self.x.unsqueeze(3)
                .repeat(1, 1, 1, self.beam_size, 1)
                .view(
                    2, -1, self.batch_size * self.beam_size, self.num_candidates
                )
            )
        # TODO add comments
        r = torch.Tensor(
            self.max_enc_len,
            2,
            self.batch_size * self.beam_size,
            self.num_candidates,
        ).to(self.device)
        r.fill_(-np.inf)

        if prefix_length == 0:
            r[0, 0] = x_inflate[0, 0]

        # TODO: scores for candidates

        # 0. phi = prev_nonblank + prev_blank = r_t-1^nb(g) + r_t-1^b(g), phi only depends on prefix g.
        r_sum = torch.logsumexp(r_prev, 1)
        phi = r_sum.unsqueeze(2).repeat(1, 1, self.num_candidates)

        # if last token of prefix g in candidates, phi = prev_b + 0
        if candidates is not None:
            for i in range(self.batch_size * self.beam_size):
                pos = scoring_table[i, last_char[i]]
                if pos != -1:
                    phi[:, i, pos] = r_prev[:, 1, i]
        else:
            for i in range(self.batch_size * self.beam_size):
                phi[:, i, last_char[i]] = r_prev[:, 1, i]

        # Define start, end, |g| < |h| for ctc decoding.
        start = max(1, prefix_length)
        end = self.max_enc_len

        # Compute forward prob log(r_t^nb(h)) and log(r_t^b(h))
        for t in range(start, end):
            # 1. p(h|cur step is nonblank) = [p(prev step=y) + phi] * p(c)
            r[t, 0] = torch.logsumexp(
                torch.stack((r[t - 1, 0], phi[t - 1]), dim=0), dim=0
            )
            r[t, 0] = r[t, 0] + x_inflate[0, t]
            # 2. p(h|cur step is blank) = [p(prev step is blank) + p(prev step is nonblank)] * p(blank)
            r[t, 1] = torch.logsumexp(
                torch.stack((r[t - 1, 0], r[t - 1, 1]), dim=0), dim=0
            )
            r[t, 1] = r[t, 1] + x_inflate[1, t]

        # Compute the predix prob
        psi_init = r[start - 1, 0].unsqueeze(0)
        # phi is prob at t-1 step, shift one frame then add it to current prob p(c)
        phix = torch.cat((phi[0].unsqueeze(0), phi[:-1]), dim=0) + x_inflate[0]
        # 3. psi = psi + phi * p(c)
        if candidates is not None:
            psi = torch.Tensor(
                self.batch_size * self.beam_size, self.vocab_size
            ).to(self.device)
            psi.fill_(-np.inf)
            psi_ = torch.logsumexp(
                torch.cat((phix[start:end], psi_init), dim=0), dim=0
            )
            # only assign prob to candidates
            for i in range(self.batch_size * self.beam_size):
                psi[i, candidates[i]] = psi_[i]
        else:
            psi = torch.logsumexp(
                torch.cat((phix[start:end], psi_init), dim=0), dim=0
            )

        # if c = <eos>, psi = log(r_T^n(g) + r_T^b(g)), where T is the max frames index of enc_states
        for i in range(self.batch_size * self.beam_size):
            psi[i, self.eos_index] = r_sum[
                self.last_frame_index[i // self.beam_size], i
            ]

        # exclude blank probs for joint scoring
        psi[:, self.blank_index] = -np.inf

        return psi - psi_prev, (r, psi, scoring_table)

    def permute_mem(self, memory, index):
        r, psi, scoring_table = memory
        # The index of top-K vocab came from in (t-1) timesteps.
        best_index = (
            index
            + (self.beam_offset.unsqueeze(1).expand_as(index) * self.vocab_size)
        ).view(-1)
        # synchoronize forward prob
        psi = torch.index_select(psi.view(-1), dim=0, index=best_index)
        psi = (
            psi.view(-1, 1)
            .repeat(1, self.vocab_size)
            .view(self.batch_size * self.beam_size, self.vocab_size)
        )

        # synchoronize ctc states
        if scoring_table is not None:
            effective_index = (
                index // self.vocab_size + self.beam_offset.view(-1, 1)
            ).view(-1)
            selected_vocab = (index % self.vocab_size).view(-1)
            score_index = scoring_table[effective_index, selected_vocab]
            score_index[score_index == -1] = 0
            best_index = score_index + effective_index * self.num_candidates

        r = torch.index_select(
            r.view(
                -1, 2, self.batch_size * self.beam_size * self.num_candidates
            ),
            dim=-1,
            index=best_index,
        )
        r = r.view(-1, 2, self.batch_size * self.beam_size)

        return r, psi


def filter_ctc_output(string_pred, blank_id=-1):
    """Apply CTC output merge and filter rules.

    Removes the blank symbol and output repetitions.

    Parameters
    ----------
    string_pred : list
        a list containing the output strings/ints predicted by the CTC system
    blank_id : int, string
        the id of the blank

    Returns
    ------
    list
        The output predicted by CTC without the blank symbol and
        the repetitions

    Example
    -------
        >>> string_pred = ['a','a','blank','b','b','blank','c']
        >>> string_out = filter_ctc_output(string_pred, blank_id='blank')
        >>> print(string_out)
        ['a', 'b', 'c']
    """

    if isinstance(string_pred, list):
        # Filter the repetitions
        string_out = [
            v
            for i, v in enumerate(string_pred)
            if i == 0 or v != string_pred[i - 1]
        ]

        # Remove duplicates
        string_out = [i[0] for i in groupby(string_out)]

        # Filter the blank symbol
        string_out = list(filter(lambda elem: elem != blank_id, string_out))
    else:
        raise ValueError("filter_ctc_out can only filter python lists")
    return string_out


def ctc_greedy_decode(probabilities, seq_lens, blank_id=-1):
    """
    Greedy decode a batch of probabilities and apply CTC rules

    Parameters
    ----------
    probabilities : torch.tensor
        Output probabilities (or log-probabilities) from network with shape
        [batch, probabilities, time]
    seq_lens : torch.tensor
        Relative true sequence lengths (to deal with padded inputs),
        longest sequence has length 1.0, others a value betwee zero and one
        shape [batch, lengths]
    blank_id : int, string
        The blank symbol/index. Default: -1. If a negative number is given,
        it is assumed to mean counting down from the maximum possible index,
        so that -1 refers to the maximum possible index.

    Returns
    -------
    list
        Outputs as Python list of lists, with "ragged" dimensions; padding
        has been removed.

    Example
    -------
        >>> import torch
        >>> probs = torch.tensor([[[0.3, 0.7], [0.0, 0.0]],
        ...                       [[0.2, 0.8], [0.9, 0.1]]])
        >>> lens = torch.tensor([0.51, 1.0])
        >>> blank_id = 0
        >>> ctc_greedy_decode(probs, lens, blank_id)
        [[1], [1]]
    """
    if isinstance(blank_id, int) and blank_id < 0:
        blank_id = probabilities.shape[-1] + blank_id
    batch_max_len = probabilities.shape[1]
    batch_outputs = []
    for seq, seq_len in zip(probabilities, seq_lens):
        actual_size = int(torch.round(seq_len * batch_max_len))
        scores, predictions = torch.max(seq.narrow(0, 0, actual_size), dim=1)
        out = filter_ctc_output(predictions.tolist(), blank_id=blank_id)
        batch_outputs.append(out)
    return batch_outputs
