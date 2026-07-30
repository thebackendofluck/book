# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

from .base import StateCollector

# US states
from .ny import NewYorkCollector
from .nj import NewJerseyCollector
from .pa import PennsylvaniaCollector
from .mi import MichiganCollector
from .ct import ConnecticutCollector
from .ma import MassachusettsCollector
from .de import DelawareCollector
from .md import MarylandCollector
from .nv import NevadaCollector
from .wv import WestVirginiaCollector
from .co import ColoradoCollector
from .va import VirginiaCollector
from .az import ArizonaCollector
from .oh import OhioCollector
from .in_ import IndianaCollector
from .ia import IowaCollector
from .tn import TennesseeCollector
from .il import IllinoisCollector  # ASP.NET POST + VIEWSTATE (real impl)
from .la import LouisianaCollector
from .ri import RhodeIslandCollector
from .mo import MissouriCollector
from .ks import KansasCollector

# International
from .uk import UKGamblingCommissionCollector
from .es import SpainCollector
from .it import ItalyCollector
from .de_country import GermanyCollector  # alias to avoid clash with .de (Delaware)
from .nl import NetherlandsCollector
from .se import SwedenCollector
from .dk import DenmarkCollector
from .fr import FranceCollector
from .on import OntarioCollector
from .br import BrazilCollector
from .mx import MexicoCollector
from .ro import RomaniaCollector
from .gr import GreeceCollector
from .be import BelgiumCollector
from .no import NorwayCollector
from .mt import MaltaCollector
from .pt import PortugalCollector

ALL_COLLECTORS: list[type[StateCollector]] = [
    # US iGaming + sports
    NewJerseyCollector, PennsylvaniaCollector, MichiganCollector,
    ConnecticutCollector, DelawareCollector, WestVirginiaCollector,
    # US Casino + sports
    NewYorkCollector, NevadaCollector, MarylandCollector, OhioCollector,
    IowaCollector, LouisianaCollector, MissouriCollector,
    # US Casino-only (sports operated through state lottery)
    KansasCollector,
    # US iGaming + sports + casino (state-operator regulator)
    RhodeIslandCollector,
    # US Sports only
    MassachusettsCollector, IllinoisCollector, IndianaCollector,
    VirginiaCollector, ColoradoCollector, ArizonaCollector, TennesseeCollector,
    # Europe
    UKGamblingCommissionCollector, SpainCollector, ItalyCollector,
    GermanyCollector, NetherlandsCollector, SwedenCollector,
    DenmarkCollector, FranceCollector, BelgiumCollector,
    # Americas (other than US)
    OntarioCollector, BrazilCollector, MexicoCollector,
    # Eastern Europe
    RomaniaCollector,
    # Southern Europe
    GreeceCollector,
    # Nordic — state monopoly
    NorwayCollector,
    # Mediterranean / world's #1 iGaming jurisdiction
    MaltaCollector,
    # Iberian Peninsula
    PortugalCollector,
]
