# Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

from loadsim import Replicant
import datetime
import time
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt


class Nexus:
    def __init__(self, definition, replicant_count, recruitment_rate):
        self.replicant_count = replicant_count
        self.recruitment_rate = recruitment_rate
        self.definition = definition
        self.renegades = []
        self.first_time = datetime.datetime.now(datetime.timezone.utc).timestamp()
        self.start_time = datetime.datetime.now(datetime.timezone.utc).timestamp()
        self.timeseries = []
        self.timeseries_plot = None
        self.final_report = {"renegades": 0, "actions": {"register": 0, "login": 0}}

    def recruit(self):
        j = 0
        for i in range(self.replicant_count):
            if j % self.recruitment_rate == 0:
                self.start_time += 1
            renegade = Replicant(definition)
            renegade.implant_memories()
            for i in range(len(renegade.false_memories)):
                renegade.false_memories[i]["wait"] += self.start_time

            self.renegades.append(renegade.false_memories)
            j += 1

    def organize(self):
        for renegade in self.renegades:
            user = renegade[0]["data"]["username"]
            for action in renegade:
                self.timeseries.append(
                    {
                        "renegade": user,
                        "time": action["wait"],
                        "action": action["action"],
                    }
                )
        timeseries = pd.DataFrame(self.timeseries)
        timeseries["time"] = timeseries["time"] - self.first_time
        self.timeseries = timeseries
        self.timeseries_plot = sns.distplot(
            self.timeseries["time"], hist=True, rug=False
        )

    def reflect(self):
        for renegade in self.renegades:
            self.final_report["renegades"] += 1


if __name__ == "__main__":
    import yaml
    from pprint import pprint
    import sys

    bettor_type = sys.argv[0]

    with open(f"../schemas/{bettor_type}.yaml", "r") as f:
        definition = yaml.load(f, Loader=yaml.FullLoader)

    batty = Nexus(definition, 10000, 2)
    batty.recruit()
    batty.organize()
    batty.reflect()
    print(batty.timeseries)
    plt.show()
    # pprint(batty.final_report)
