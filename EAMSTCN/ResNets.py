"""
https://github.com/hkchengrex/STCN, https://github.com/seoungwugoh/STM
STCN: GitHub code for the encoders. Modified to fit EAMSTCN producing an OrderedDict() of the stage outputs.
Used to provide a control model to compare with the ones based on EfficientNets
"""

import torch.nn.functional as F
from torchvision import models
from collections import OrderedDict
import math
import torch
import torch.nn as nn
from torch.utils import model_zoo


class ResValueEncoder(nn.Module):
    def __init__(self):
        super().__init__()
        resnet = resnet18(pretrained=True, extra_chan=2)
        self.conv1 = resnet.conv1
        self.bn1 = resnet.bn1
        self.relu = resnet.relu  # 1/2, 64
        self.maxpool = resnet.maxpool

        self.layer1 = resnet.layer1  # 1/4, 64
        self.layer2 = resnet.layer2  # 1/8, 128
        self.layer3 = resnet.layer3  # 1/16, 256

    def forward(self, x):
        # key_f16 is the feature from the key encoder
        features = OrderedDict()

        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)  # 1/2, 64
        features['stage_1'] = x
        x = self.maxpool(x)  # 1/4, 64
        x = self.layer1(x)  # 1/4, 64
        features['stage_2'] = x
        x = self.layer2(x)  # 1/8, 128
        features['stage_3'] = x
        x = self.layer3(x)  # 1/16, 256
        features['stage_4'] = x

        return features


class ResKeyEncoder(nn.Module):
    def __init__(self):
        super().__init__()
        resnet = models.resnet50(weights='ResNet50_Weights.DEFAULT')
        self.conv1 = resnet.conv1
        self.bn1 = resnet.bn1
        self.relu = resnet.relu  # 1/2, 64
        self.maxpool = resnet.maxpool

        self.res2 = resnet.layer1  # 1/4, 256
        self.layer2 = resnet.layer2  # 1/8, 512
        self.layer3 = resnet.layer3  # 1/16, 1024

    def forward(self, f):
        features = OrderedDict()
        x = self.conv1(f)
        x = self.bn1(x)
        x = self.relu(x)  # 1/2, 64
        features['stage_1'] = x
        x = self.maxpool(x)  # 1/4, 64
        f4 = self.res2(x)  # 1/4, 256
        features['stage_2'] = f4
        f8 = self.layer2(f4)  # 1/8, 512
        features['stage_3'] = f8
        f16 = self.layer3(f8)  # 1/16, 1024
        features['stage_4'] = f16
        return features


def load_weights_sequential(target, source_state, extra_chan=1):
    new_dict = OrderedDict()

    for k1, v1 in target.state_dict().items():
        if not 'num_batches_tracked' in k1:
            if k1 in source_state:
                tar_v = source_state[k1]

                if v1.shape != tar_v.shape:
                    # Init the new segmentation channel with zeros
                    # print(v1.shape, tar_v.shape)
                    c, _, w, h = v1.shape
                    pads = torch.zeros((c, extra_chan, w, h), device=tar_v.device)
                    nn.init.orthogonal_(pads)
                    tar_v = torch.cat([tar_v, pads], 1)

                new_dict[k1] = tar_v

    target.load_state_dict(new_dict, strict=False)


model_urls = {
    'resnet18': 'https://download.pytorch.org/models/resnet18-5c106cde.pth',
    'resnet50': 'https://download.pytorch.org/models/resnet50-19c8e357.pth',
}


def conv3x3(in_planes, out_planes, stride=1, dilation=1):
    return nn.Conv2d(in_planes, out_planes, kernel_size=3, stride=stride,
                     padding=dilation, dilation=dilation)


class BasicBlock(nn.Module):
    expansion = 1

    def __init__(self, inplanes, planes, stride=1, downsample=None, dilation=1):
        super(BasicBlock, self).__init__()
        self.conv1 = conv3x3(inplanes, planes, stride=stride, dilation=dilation)
        self.bn1 = nn.BatchNorm2d(planes)
        self.relu = nn.ReLU(inplace=True)
        self.conv2 = conv3x3(planes, planes, stride=1, dilation=dilation)
        self.bn2 = nn.BatchNorm2d(planes)
        self.downsample = downsample
        self.stride = stride

    def forward(self, x):
        residual = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.conv2(out)
        out = self.bn2(out)

        if self.downsample is not None:
            residual = self.downsample(x)

        out += residual
        out = self.relu(out)

        return out


class Bottleneck(nn.Module):
    expansion = 4

    def __init__(self, inplanes, planes, stride=1, downsample=None, dilation=1):
        super(Bottleneck, self).__init__()
        self.conv1 = nn.Conv2d(inplanes, planes, kernel_size=1)
        self.bn1 = nn.BatchNorm2d(planes)
        self.conv2 = nn.Conv2d(planes, planes, kernel_size=3, stride=stride, dilation=dilation,
                               padding=dilation)
        self.bn2 = nn.BatchNorm2d(planes)
        self.conv3 = nn.Conv2d(planes, planes * 4, kernel_size=1)
        self.bn3 = nn.BatchNorm2d(planes * 4)
        self.relu = nn.ReLU(inplace=True)
        self.downsample = downsample
        self.stride = stride

    def forward(self, x):
        residual = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.conv2(out)
        out = self.bn2(out)
        out = self.relu(out)

        out = self.conv3(out)
        out = self.bn3(out)

        if self.downsample is not None:
            residual = self.downsample(x)

        out += residual
        out = self.relu(out)

        return out


class ResNet(nn.Module):
    def __init__(self, block, layers=(3, 4, 23, 3), extra_chan=1):
        self.inplanes = 64
        super().__init__()
        self.conv1 = nn.Conv2d(3 + extra_chan, 64, kernel_size=7, stride=2, padding=3)
        self.bn1 = nn.BatchNorm2d(64)
        self.relu = nn.ReLU(inplace=True)
        self.maxpool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)
        self.layer1 = self._make_layer(block, 64, layers[0])
        self.layer2 = self._make_layer(block, 128, layers[1], stride=2)
        self.layer3 = self._make_layer(block, 256, layers[2], stride=2)
        self.layer4 = self._make_layer(block, 512, layers[3], stride=2)

        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
                m.weight.data.normal_(0, math.sqrt(2. / n))
                m.bias.data.zero_()
            elif isinstance(m, nn.BatchNorm2d):
                m.weight.data.fill_(1)
                m.bias.data.zero_()

    def _make_layer(self, block, planes, blocks, stride=1, dilation=1):
        downsample = None
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                nn.Conv2d(self.inplanes, planes * block.expansion,
                          kernel_size=1, stride=stride),
                nn.BatchNorm2d(planes * block.expansion),
            )

        layers = [block(self.inplanes, planes, stride, downsample)]
        self.inplanes = planes * block.expansion
        for i in range(1, blocks):
            layers.append(block(self.inplanes, planes, dilation=dilation))

        return nn.Sequential(*layers)


def resnet18(pretrained=True, extra_chan=0):
    model = ResNet(BasicBlock, [2, 2, 2, 2], extra_chan)
    if pretrained:
        load_weights_sequential(model, model_zoo.load_url(model_urls['resnet18']), extra_chan)
    return model


def resnet50(pretrained=True, extra_chan=0):
    model = ResNet(Bottleneck, [3, 4, 6, 3], extra_chan)
    if pretrained:
        load_weights_sequential(model, model_zoo.load_url(model_urls['resnet50']), extra_chan)
    return model
