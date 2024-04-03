# --------------------------------------------------------
# Based on BEiT, timm, DINO and DeiT code bases
# https://github.com/microsoft/unilm/tree/master/beit
# https://github.com/rwightman/pytorch-image-models/tree/master/timm
# https://github.com/facebookresearch/deit
# https://github.com/facebookresearch/dino
# --------------------------------------------------------'
import os

from timm.data import create_transform
from timm.data.constants import (
    IMAGENET_DEFAULT_MEAN,
    IMAGENET_DEFAULT_STD,
    IMAGENET_INCEPTION_MEAN,
    IMAGENET_INCEPTION_STD,
)
from torchvision import transforms

from mae import HybridVideoMAE
from masking_generator import (
    RandomMaskingGenerator,
    TemporalCenteringProgressiveMaskingGenerator,
    TemporalConsistencyMaskingGenerator,
    TemporalProgressiveMaskingGenerator,
)
from ek100 import EKRawFrameClsDataset
from transforms import *


class DataAugmentationForMAE(object):

    def __init__(self, args):
        imagenet_default_mean_and_std = args.imagenet_default_mean_and_std
        mean = IMAGENET_INCEPTION_MEAN if not imagenet_default_mean_and_std else IMAGENET_DEFAULT_MEAN
        std = IMAGENET_INCEPTION_STD if not imagenet_default_mean_and_std else IMAGENET_DEFAULT_STD
        self.input_mean = [0.485, 0.456, 0.406]
        self.input_std = [0.229, 0.224, 0.225]
        div = True
        roll = False
        normalize = GroupNormalize(self.input_mean, self.input_std)
        self.train_augmentation = GroupMultiScaleCrop(args.input_size,
                                                      [1, .875, .75, .66])
        self.transform = transforms.Compose([
            # GroupScale((240,320)),
            self.train_augmentation,
            Stack(roll=roll),
            ToTorchFormatTensor(div=div),
            normalize,
        ])
        if args.mask_type == 'random':
            self.masked_position_generator = RandomMaskingGenerator(
                args.window_size, args.mask_ratio)
        elif args.mask_type == 't_consist':
            self.masked_position_generator = TemporalConsistencyMaskingGenerator(
                args.window_size, args.mask_ratio)
        elif args.mask_type == 't_progressive':
            self.masked_position_generator = TemporalProgressiveMaskingGenerator(
                args.window_size, args.mask_ratio)
        elif args.mask_type == 't_center_prog':
            self.masked_position_generator = TemporalCenteringProgressiveMaskingGenerator(
                args.window_size, args.mask_ratio)

    def __call__(self, images):
        process_data, _ = self.transform(images)
        return process_data, self.masked_position_generator()

    def __repr__(self):
        repr = "(DataAugmentationForBEiT,\n"
        repr += "  transform = %s,\n" % str(self.transform)
        repr += "  Masked position generator = %s,\n" % str(
            self.masked_position_generator)
        repr += ")"
        return repr


def build_pretraining_dataset(args):
    transform = DataAugmentationForMAE(args)
    dataset = HybridVideoMAE(
        root=args.data_root,
        # dataset = VideoMAE(root=args.data_root,
        setting=args.data_path,
        train=True,
        test_mode=False,
        video_ext='mp4',
        is_color=True,
        modality='rgb',
        num_segments=1,
        num_crop=1,
        new_length=args.num_frames,
        new_step=args.sampling_rate,
        transform=transform,
        temporal_jitter=False,
        video_loader=True,
        use_decord=True,
        lazy_init=False,
        num_sample=args.num_sample)
    print("Data Aug = %s" % str(transform))
    return dataset


def build_dataset(is_train, test_mode, args):

    if args.data_set == 'EK100':
        mode = None
        anno_path = None
        if is_train == True:
            mode = 'train'
            anno_path = os.path.join(args.anno_path, 'EPIC_100_train.csv')
        elif test_mode == True:
            mode = 'test'
            anno_path = os.path.join(args.anno_path, 'EPIC_100_validation.csv')
        else:
            mode = 'validation'
            anno_path = os.path.join(args.anno_path, 'EPIC_100_validation.csv')

        dataset = EKRawFrameClsDataset(
                                anno_path=anno_path,
                                data_path=args.data_path,
                                mode=mode,
                                clip_len=1,
                                num_segment=args.num_frames,
                                test_num_segment=args.test_num_segment,
                                test_num_crop=args.test_num_crop,
                                num_crop=1 if not test_mode else 3,
                                keep_aspect_ratio=True,
                                crop_size=args.input_size,
                                short_side_size=args.short_side_size,
                                new_height=256,
                                new_width=320,
                                filename_tmpl='frame_{:010}.jpg',
                                args=args
                            )
        nb_classes = [97, 300]
    else:
        raise NotImplementedError()
    assert nb_classes == args.nb_classes
    print(f"Number of the class = {args.nb_classes}")

    return dataset, nb_classes


def build_transform(is_train, test_mode, args):
    resize_im = args.input_size > 32
    imagenet_default_mean_and_std = args.imagenet_default_mean_and_std
    mean = IMAGENET_INCEPTION_MEAN if not imagenet_default_mean_and_std else IMAGENET_DEFAULT_MEAN
    std = IMAGENET_INCEPTION_STD if not imagenet_default_mean_and_std else IMAGENET_DEFAULT_STD

    if is_train:
        # this should always dispatch to transforms_imagenet_train
        transform = create_transform(
            input_size=args.input_size,
            is_training=True,
            color_jitter=args.color_jitter,
            auto_augment=args.aa,
            interpolation=args.train_interpolation,
            re_prob=args.reprob,
            re_mode=args.remode,
            re_count=args.recount,
            mean=mean,
            std=std,
        )
        if not resize_im:
            # replace RandomResizedCropAndInterpolation with
            # RandomCrop
            transform.transforms[0] = transforms.RandomCrop(args.input_size,
                                                            padding=4)
        return transform

    t = []
    if resize_im:
        if args.crop_pct is None:
            if args.input_size < 384:
                args.crop_pct = 224 / 256
            else:
                args.crop_pct = 1.0
        size = int(args.input_size / args.crop_pct)
        t.append(
            transforms.Resize(
                size,
                interpolation=3),  # to maintain same ratio w.r.t. 224 images
        )
        t.append(transforms.CenterCrop(args.input_size))

    t.append(transforms.ToTensor())
    t.append(transforms.Normalize(mean, std))
    return transforms.Compose(t)
