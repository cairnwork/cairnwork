"""Pinned row recipes; compressed to keep the data module reviewable and under 400 lines."""

import base64
import json
import zlib

FIELDS = ("seq", "recipe_id", "class_id", "probe", "route", "expected", "classification", "reason")
_DATA = (
    "c-qaFU2~#H6o&r_m8+eqkhMSXYj4&VGI67jQry|9Y>GncOxPgpG?2vo_0zNmwT6znHA%!93x"
    "q!Jn{!S-ea!7`<Mc%EXS2~bIX&qk`SHX=biXpgI5M}lzPvR=p2#o$w8w+Xfqfw_e;0Yoy6TN"
    "646=wP^1UEUH@DsLW&g|Abv~bt?aS%p$E7P@ups5h$=&HK0yn61GmC@pamHUXUJTPLpUP*B>"
    "-poUvk6^3`oU54`Bof*I9B5RXzKMGPhPX9AeBo^!Ip9no13$hxs*Y5*}3cuhW|4+SFU4EJik"
    "oQDctKK?52o-OQI<lK6t?>xS^GT;g2tv1>%+_73AQlXo8z&B^Bi3Q_%wPRw0h--o|Zk(=Oc{"
    "avOI*yraaAbsHl9FKTEicKCgafV>D*N_K#ai~zkzp_kNt3ISsgDeWAxe-SVhDW;NY1EXVUVc"
    ";*aG*zpig#o~*S*h9~S{Mk7TCY?MA4}B$&Pc(T*1p#O*Qit4Iq1j-6vm3e^jHrI40OgwQ?tY"
    "GGz_@LxKgtN>@*B?#){4bd*Ub%W|U+#v@Zr&V_Gp&LtOyKxbU%VEqM`gAy}BLe^kBA(9gCnk"
    "Qq0%zHn{6fZ4dE^+js)1&GFNtuI=eFJLw9Xnk3A`657Z($M;9*5(VaO_0`Ct2ST2Z-TYH+O_"
    "!tvJ;~9)v3)FK%P*YFZBzq9$&zFq6YAy&0~4Tl1%XB%FIO0c8+HMpM3t6ovBZM?eV5JnQWpL"
    "(@pghRx*HZ($vZP%LIQP8K5y~>15bqnXTSO2k1)F0PH&6*d59%{+uvjLQ(e%trbFESc`r3vX"
    "m>>eA65LPVH4xW5MUFYs{k}w4ovyJc(Do8H*qbAK2Uod7flOlp1`NGVv75Vy3JDa7jmF^;z)"
    "b>)VWO8tiR0De#x7DY__MU?zNb?AvaSGk-l33id6RLgrHNOv<gxGj(ppu4nf>WokR`C=q}uM"
    "Wx8h<OwsgV6kLEPUy>3#?nkTe`>YfVG^alQL5(PqDEP?U_Aei_F1<_zvm5H#~(O-cXTtfJ$q"
    "ZPj=pgz;E|G2P9`}BRZ%`j5x_^+|K2UiD+3nImnPBuy_&aW+8?;g@{M6G3_lDakvwzp=x_6-"
    "R{Wh~)WLf^mXEr@eBL#Ry=>f;eTRNUck-2M$Nl2?V|y~VnD&NS_o%U36o4YN^q%oCdIrMMrr"
    "t9-M$dq2s&K7lh9HjS+x<7olX+a6K<@Q6MB7(yz0z(#X4)=8KCnO+@yhO9_@lvO)bsjRRZ;*"
    "oX{QV`3sR;DbKaZSuZOXKYF48RQ}D$P`C<`enrL1@)QLMC_xA+_idm=(%3fHQODg-BU^<iD*"
    "xnZp5M<%a-@4(LGP+=i&g}WH*S|h@|MDdZMgwnTJKi1<0FOngA`dKlO!o?0+5PKD5qvUmK38"
    "J{Vk}x>J(Ro-StJ9N;7&D1$y(@_C6A+U^+|>OLw|5s00hh0G-~4vnR2~UfFq$eK2)TitLxo&"
    "E<A}P"
)
RECIPES = tuple(tuple(row) for row in json.loads(zlib.decompress(base64.b85decode("".join(_DATA)))))
