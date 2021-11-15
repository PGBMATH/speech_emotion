import torch
import torch.nn


def test_pooling1d():

    from speechbrain.nnet.pooling import Pooling1d

    input = torch.tensor([1, 3, 2]).unsqueeze(0).unsqueeze(-1).float()
    pool = Pooling1d("max", 3)
    output, _ = pool(input)
    assert output == 3

    pool = Pooling1d("avg", 3)
    output, _ = pool(input)
    assert output == 2

    assert torch.jit.trace(pool, [input, input.eq(0.0)])


def test_pooling2d():

    from speechbrain.nnet.pooling import Pooling2d

    input = torch.tensor([[1, 3, 2], [4, 6, 5]]).float().unsqueeze(0)
    pool = Pooling2d("max", (2, 3))
    output, _ = pool(input)
    assert output == 6

    input = torch.tensor([[1, 3, 2], [4, 6, 5]]).float().unsqueeze(0)
    pool = Pooling2d("max", (1, 3))
    output, _ = pool(input)
    assert output[0][0] == 3
    assert output[0][1] == 6

    input = torch.tensor([[1, 3, 2], [4, 6, 5]]).float().unsqueeze(0)
    pool = Pooling2d("avg", (2, 3))
    output, _ = pool(input)
    assert output == 3.5

    input = torch.tensor([[1, 3, 2], [4, 6, 5]]).float().unsqueeze(0)
    pool = Pooling2d("avg", (1, 3))
    output, _ = pool(input)
    assert output[0][0] == 2
    assert output[0][1] == 5

    assert torch.jit.trace(pool, [input, input.eq(0.0)])


def test_pooling1d_with_padding():

    from speechbrain.nnet.pooling import Pooling1d

    input = torch.tensor([-1, -3, -2, 0, 0]).unsqueeze(0).unsqueeze(-1).float()
    mask = ~torch.tensor([1, 1, 1, 0, 0]).unsqueeze(0).unsqueeze(-1).bool()
    pool = Pooling1d("max", 5)
    output, _ = pool(input, mask)
    assert output == -1

    pool = Pooling1d("avg", 5)
    output, _ = pool(input, mask)
    assert output == -2


def test_pooling2d_with_padding():

    from speechbrain.nnet.pooling import Pooling2d

    input = (
        torch.tensor([[-1, -3, -2, 0, 0], [-4, -6, -5, 0, 0]])
        .float()
        .unsqueeze(0)
    )
    mask = ~torch.tensor([[1, 1, 1, 0, 0], [1, 1, 1, 0, 0]]).unsqueeze(0).bool()
    pool = Pooling2d("max", (2, 5))
    output, _ = pool(input, mask)
    assert output == -1

    input = (
        torch.tensor([[-1, -3, -2, 0], [-4, -6, -5, 0]]).float().unsqueeze(0)
    )
    mask = ~torch.tensor([[1, 1, 1, 0], [1, 1, 1, 0]]).unsqueeze(0).bool()
    pool = Pooling2d("max", (1, 4))
    output, _ = pool(input, mask)
    assert output[0][0] == -1
    assert output[0][1] == -4

    input = torch.tensor([[1, 3, 2, 0], [4, 6, 5, 0]]).float().unsqueeze(0)
    mask = ~torch.tensor([[1, 1, 1, 0], [1, 1, 1, 0]]).unsqueeze(0).bool()
    pool = Pooling2d("avg", (2, 4))
    output, _ = pool(input, mask)
    assert output == 3.5

    input = torch.tensor([[1, 3, 2, 0], [4, 6, 5, 0]]).float().unsqueeze(0)
    mask = ~torch.tensor([[1, 1, 1, 0], [1, 1, 1, 0]]).unsqueeze(0).bool()
    pool = Pooling2d("avg", (1, 4))
    output, _ = pool(input, mask)
    assert output[0][0] == 2
    assert output[0][1] == 5

    assert torch.jit.trace(pool, [input, input.eq(0.0)])
