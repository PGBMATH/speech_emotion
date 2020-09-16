import torch


def test_saveable_dataloader(tmpdir):
    from speechbrain.data_io.dataloader import SaveableDataLoader

    save_file = tmpdir + "/dataloader.ckpt"
    dataset = torch.randn(10, 1)
    dataloader = SaveableDataLoader(dataset)
    data_iterator = iter(dataloader)
    first_item = next(data_iterator)
    assert first_item == dataset[0]
    # Save here:
    dataloader._speechbrain_save(save_file)
    second_item = next(data_iterator)
    assert second_item == dataset[1]
    # Now make a new dataloader and recover:
    new_dataloader = SaveableDataLoader(dataset)
    new_dataloader._speechbrain_load(save_file, end_of_epoch=False)
    new_data_iterator = iter(new_dataloader)
    second_second_item = next(new_data_iterator)
    assert second_second_item == second_item


def test_saveable_dataloader_multiprocess(tmpdir):
    # Same test as above, but with multiprocess dataloading
    from speechbrain.data_io.dataloader import SaveableDataLoader

    save_file = tmpdir + "/dataloader.ckpt"
    dataset = torch.randn(10, 1)
    for num_parallel in [1, 2, 10, 12]:
        dataloader = SaveableDataLoader(
            dataset, num_workers=num_parallel
        )  # Note num_workers
        data_iterator = iter(dataloader)
        first_item = next(data_iterator)
        assert first_item == dataset[0]
        # Save here, note that this overwrites.
        dataloader._speechbrain_save(save_file)
        second_item = next(data_iterator)
        assert second_item == dataset[1]
        # Now make a new dataloader and recover:
        new_dataloader = SaveableDataLoader(dataset, num_workers=num_parallel)
        new_dataloader._speechbrain_load(save_file, end_of_epoch=False)
        new_data_iterator = iter(new_dataloader)
        second_second_item = next(new_data_iterator)
        assert second_second_item == second_item
