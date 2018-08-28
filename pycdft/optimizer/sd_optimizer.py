from __future__ import absolute_import, division, print_function

from .base import Optimizer


class SDOptimizer(Optimizer):
    def __init__(self, step=0.01):
        self.step = step
        self.x = None

    def setup(self):
        pass

    # def update(self, dy_by_dx):
    #     self.x += self.step * dy_by_dx
    #     return self.x

    def update(self, dy_by_dx, x, y=None):
        return x + self.step * dy_by_dx

