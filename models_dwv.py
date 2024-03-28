# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.

# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.
# --------------------------------------------------------
# References:
# timm: https://github.com/rwightman/pytorch-image-models/tree/master/timm
# DeiT: https://github.com/facebookresearch/deit
# --------------------------------------------------------

from functools import partial
from einops.layers.torch import Rearrange
from wave_dynamic_layer import Dynamic_MLP_OFA, Dynamic_MLP_Decoder
from operator import mul
from torch.nn.modules.utils import _pair
from torch.nn import Conv2d, Dropout
import numpy as np

import torch
import torch.nn as nn
import pdb
import math
from functools import reduce
import json

from timm.models.vision_transformer import PatchEmbed, Block
from util.pos_embed import get_2d_sincos_pos_embed, get_1d_sincos_pos_embed_from_grid_torch

class OFAViT(nn.Module):
    """ Masked Autoencoder with VisionTransformer backbone
    """
    def __init__(self, img_size=224, patch_size=16, drop_rate=0.,
                 embed_dim=1024, depth=24, num_heads=16, wv_planes=128, num_classes=45,
                 global_pool=True, mlp_ratio=4., norm_layer=nn.LayerNorm):
        super().__init__()

        self.wv_planes = wv_planes
        self.global_pool = global_pool
        if self.global_pool:
            norm_layer = norm_layer
            embed_dim = embed_dim
            self.fc_norm = norm_layer(embed_dim)
        else:
            self.norm = norm_layer(embed_dim)

        self.patch_embed = Dynamic_MLP_OFA(wv_planes=128, inter_dim=128, kernel_size=16, embed_dim=embed_dim)
        self.num_patches = (img_size // patch_size) ** 2
        self.cls_token = nn.Parameter(torch.zeros(1, 1, embed_dim))
        self.pos_embed = nn.Parameter(torch.zeros(1, self.num_patches + 1, embed_dim), requires_grad=False)  # fixed sin-cos embedding

        self.blocks = nn.ModuleList([
            Block(embed_dim, num_heads, mlp_ratio, qkv_bias=True, norm_layer=norm_layer)
            for i in range(depth)])

        self.head_drop = nn.Dropout(drop_rate)
        self.head = nn.Linear(embed_dim, num_classes) if num_classes > 0 else nn.Identity()


    def forward_features(self, x, wave_list):
        # embed patches
        wavelist = torch.tensor(wave_list, device=x.device).float()
        self.waves = wavelist

        x, _ = self.patch_embed(x, self.waves)

        x = x + self.pos_embed[:, 1:, :]
        # append cls token
        cls_token = self.cls_token + self.pos_embed[:, :1, :]
        cls_tokens = cls_token.expand(x.shape[0], -1, -1)
        x = torch.cat((cls_tokens, x), dim=1)

        # apply Transformer blocks
        for block in self.blocks:
            x = block(x)

        if self.global_pool:
            x = x[:, 1:, :].mean(dim=1)  # global pool without cls token
            outcome = self.fc_norm(x)
        else:
            x = self.norm(x)
            outcome = x[:, 0]
        return outcome

    def forward_head(self, x, pre_logits=False):
        x = self.head_drop(x)
        return x if pre_logits else self.head(x)

    def forward(self, x, wave_list):
        x = self.forward_features(x, wave_list)
        x = self.forward_head(x)
        return x


def vit_small_patch16(**kwargs):
    model = OFAViT(
        patch_size=16, embed_dim=384, depth=12, num_heads=6, mlp_ratio=4,
        norm_layer=partial(nn.LayerNorm, eps=1e-6), **kwargs)
    return model

def vit_base_patch16(**kwargs):
    model = OFAViT(
        patch_size=16, embed_dim=768, depth=12, num_heads=12, mlp_ratio=4,
        norm_layer=partial(nn.LayerNorm, eps=1e-6), **kwargs)
    return model


def vit_large_patch16(**kwargs):
    model = OFAViT(
        patch_size=16, embed_dim=1024, depth=24, num_heads=16, mlp_ratio=4,
        norm_layer=partial(nn.LayerNorm, eps=1e-6), **kwargs)
    return model


def vit_huge_patch14(**kwargs):
    model = OFAViT(
        patch_size=14, embed_dim=1280, depth=32, num_heads=16, mlp_ratio=4,
        norm_layer=partial(nn.LayerNorm, eps=1e-6), **kwargs)
    return model


import rasterio
import kornia as K
# vh,vv
S1_MEAN = [166.36275909, 88.45542715]# / 255.0
S1_STD = [64.83126309, 43.07350145]# /255.0

S2_MEAN = [114.1099739 , 114.81779093, 126.63977424,  84.33539309,
        97.84789168, 103.94461911, 101.435633  ,  72.32804172,
        56.66528851]
S2_STD = [77.84352553, 69.96844919, 67.42465279, 64.57022983, 61.72545487,
       61.34187099, 60.29744676, 47.88519516, 42.55886798]

NAIP_MEAN = [123.675, 116.28, 103.53] # ImageNet stats for now
NAIP_STD = [58.395, 57.12, 57.375] # ImageNet stats for now

Hyper_MEAN = [0.04904891, 0.04734517, 0.04881499, 0.0521312 , 0.05371449,
       0.05431649, 0.05600387, 0.05753566, 0.05837488, 0.06014395,
       0.06120129, 0.06187369, 0.06262351, 0.0640324 , 0.06544493,
       0.06583978, 0.06657578, 0.06818208, 0.06887893, 0.07050433,
       0.0730939 , 0.07500546, 0.07658557, 0.07914317, 0.08149088,
       0.08413605, 0.08584346, 0.0875968 , 0.08949799, 0.09126373,
       0.09284472, 0.09385966, 0.09429929, 0.09644857, 0.09758445,
       0.09888336, 0.1000851 , 0.1012402 , 0.10217949, 0.10292227,
       0.10441044, 0.10482953, 0.10586129, 0.10771345, 0.10875386,
       0.10872938, 0.10955398, 0.11022133, 0.11095442, 0.11202578,
       0.1144143 , 0.11816488, 0.12615164, 0.13405322, 0.14239053,
       0.14940845, 0.15952006, 0.16786492, 0.17470501, 0.17793106,
       0.18344983, 0.1717895 , 0.18624028, 0.18920699, 0.19094643,
       0.19191591, 0.19321348, 0.19543689, 0.19453696, 0.197083  ,
       0.19759373, 0.20008718, 0.20109805, 0.20273463, 0.20447857,
       0.20549563, 0.20768884, 0.20616769, 0.20696713, 0.21603036,
       0.20431315, 0.20028888, 0.21381801, 0.19553433, 0.22010142,
       0.20745818, 0.20469215, 0.20106881, 0.21624712, 0.20217295,
       0.21258003, 0.19276747, 0.19084313, 0.21547508, 0.1990552 ,
       0.2220764 , 0.20010307, 0.22556668, 0.20294108, 0.20432738,
       0.22884114, 0.23084754, 0.23361147, 0.23613915, 0.23835524,
       0.24002708, 0.24240751, 0.24512835, 0.24625339, 0.24729841,
       0.24621868, 0.2335448 , 0.23611045, 0.23486711, 0.22491438,
       0.23376624, 0.23624881, 0.23806269, 0.23892529, 0.24048481,
       0.24448228, 0.24877857, 0.25137802, 0.25356494, 0.25809049,
       0.25776253, 0.19769041, 0.20087005, 0.20429956, 0.20702436,
       0.21026749, 0.21287441, 0.21557267, 0.21920979, 0.22113606,
       0.2227512 , 0.22380759, 0.22556949, 0.22486467, 0.22534442,
       0.22393468, 0.22259283, 0.22103291, 0.21938812, 0.21726496,
       0.15029096, 0.15920778, 0.14992988, 0.1402144 , 0.14215149,
       0.16192129, 0.16663248, 0.16954731, 0.16092901, 0.16268809,
       0.16303404, 0.16922207, 0.16708257, 0.16741623, 0.16829468,
       0.16974312, 0.17043488, 0.17164688, 0.17061502, 0.17135934,
       0.17061249, 0.17054758, 0.17006451, 0.17125258, 0.16988001,
       0.16870924, 0.16756754, 0.1703907 , 0.17044655, 0.16995895,
       0.16583233, 0.16387971, 0.15977418, 0.15813546, 0.15500555,
       0.15475487, 0.1500904 , 0.14842632, 0.14555257, 0.1444372 ,
       0.14345487, 0.1439716 , 0.13948618, 0.13974816, 0.1388893 ,
       0.1425306 , 0.1399015 , 0.14124387, 0.13716652, 0.13908459,
       0.13517979, 0.13579579, 0.12699047, 0.13110322, 0.12600956,
       0.12683088, 0.11266357]

Hyper_STD = [0.05438845, 0.05425398, 0.05519192, 0.05621342, 0.05694766,
       0.05704381, 0.05763639, 0.05804253, 0.05834612, 0.05888857,
       0.05920108, 0.05948167, 0.06002252, 0.06070791, 0.06148699,
       0.06196402, 0.06251209, 0.063477  , 0.06412124, 0.06480832,
       0.06584631, 0.06655134, 0.06710579, 0.06817766, 0.06930595,
       0.07081702, 0.07204193, 0.07329206, 0.07491294, 0.07681551,
       0.07890642, 0.08096196, 0.08243044, 0.08428552, 0.08603505,
       0.08763417, 0.08869326, 0.08985463, 0.09081571, 0.09164355,
       0.09288558, 0.09362094, 0.09377934, 0.09490612, 0.09628371,
       0.09688408, 0.09770997, 0.09843717, 0.099185  , 0.09957288,
       0.10124085, 0.10085674, 0.0999966 , 0.09884673, 0.09924151,
       0.10034673, 0.10382162, 0.10637773, 0.10945914, 0.11091637,
       0.1143651 , 0.11309448, 0.11714786, 0.11771383, 0.11835992,
       0.11911578, 0.11971312, 0.12100819, 0.12134505, 0.12288926,
       0.1223688 , 0.12337272, 0.12378503, 0.1247803 , 0.12574014,
       0.12591301, 0.12706246, 0.12643132, 0.1274808 , 0.12616546,
       0.12669385, 0.12547143, 0.12466624, 0.12086743, 0.1281701 ,
       0.12939227, 0.11918587, 0.1359367 , 0.12719588, 0.13615098,
       0.12415102, 0.12939233, 0.12601028, 0.12531339, 0.12763924,
       0.12921317, 0.12880291, 0.13102435, 0.13017717, 0.13047819,
       0.13304893, 0.13441405, 0.13619871, 0.13785246, 0.13942554,
       0.14062946, 0.14236353, 0.14349961, 0.14351399, 0.14394955,
       0.14339238, 0.13597313, 0.13902099, 0.13897383, 0.13261457,
       0.1377756 , 0.13982825, 0.14176949, 0.14285451, 0.14382317,
       0.14602028, 0.1482343 , 0.15007727, 0.15194561, 0.153546  ,
       0.15312391, 0.14626474, 0.1466966 , 0.14744146, 0.14778844,
       0.14876473, 0.14891526, 0.14976731, 0.15107008, 0.15135105,
       0.15174565, 0.15199847, 0.15274257, 0.15231952, 0.15232084,
       0.1517344 , 0.15127988, 0.15108911, 0.15042051, 0.1496356 ,
       0.13592469, 0.14091663, 0.13328618, 0.11833   , 0.12461805,
       0.13769715, 0.1433956 , 0.14432674, 0.13841473, 0.13815018,
       0.13944786, 0.14269211, 0.14142959, 0.14021556, 0.14112666,
       0.14058631, 0.14122906, 0.14030282, 0.1395203 , 0.13781546,
       0.13659598, 0.13416635, 0.13343848, 0.13207203, 0.13057547,
       0.12746956, 0.12577166, 0.12779064, 0.12974892, 0.12913598,
       0.12803291, 0.12678831, 0.12626308, 0.12507224, 0.1248102 ,
       0.12383852, 0.121826  , 0.11985217, 0.11986669, 0.11762512,
       0.1181166 , 0.11759082, 0.11575512, 0.11410792, 0.11531784,
       0.1169539 , 0.11614721, 0.11519321, 0.11456495, 0.11471919,
       0.11496413, 0.11304017, 0.10994165, 0.11212726, 0.11287158,
       0.11276065, 0.10822126]

Gaufen_MEAN = [123.94924583,  92.58088583,  97.28130189,  90.31526596]
Gaufen_STD = [67.34487297, 62.8271046 , 60.5856767 , 60.3946299]

class DataAugmentation(torch.nn.Module):
    def __init__(self, mean, std):
        super().__init__()
        self.transform = torch.nn.Sequential(
            K.augmentation.RandomResizedCrop(size=(224,224), scale=(0.2,1.0)),
            K.augmentation.Normalize(mean=mean,std=std)
        )
    @torch.no_grad()
    def forward(self,x):
        #x = kornia.image_to_tensor(x_np, keepdim=True)  # CxHxW
        x_out = self.transform(x)
        return x_out

transform = DataAugmentation(mean=S1_MEAN,std=S1_STD)

def preprocess_s1(vh_path, vv_path):
    with rasterio.open(vh_path) as f1:
        vh = f1.read()
    with rasterio.open(vv_path) as f2:
        vv = f2.read()
    s1_img = np.concatenate((vh,vv),0).astype('float32')
    s1_img = torch.from_numpy(s1_img)
    s1_img = transform(s1_img).squeeze(0)
    return s1_img

if __name__=='__main__':
    check_point = torch.load('./DOFA_ViT_base_e100.pth')
    vit_model = vit_base_patch16()
    vit_model.load_state_dict(check_point, strict=True)
    vit_model = vit_model.cuda()
    C = 2  # can be 2,3,4,6,9,12,13,202 or any number if you can provide the wavelengths of them
    image1 = './data/s1/vv/1848_3569.png'
    image2 = './data/s1/vh/1848_3569.png'
    s1_img = preprocess_s1(image1,image2).cuda()
    s1_img = s1_img.view([1,2,224,224])

    with open('waves.json','r') as wf:
        wavelists = json.load(wf)
    test_out = vit_model.forward_features(s1_img, wave_list=wavelists[f'{C}'])
    print(test_out.shape)
