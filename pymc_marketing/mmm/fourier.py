#   Copyright 2024 The PyMC Labs Developers
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
"""Fourier seasonality transformations.

This modules provides Fourier seasonality transformations for use in
Marketing Mix Models. The Fourier seasonality is a set of sine and cosine
functions that can be used to model periodic patterns in the data.

There are two types of Fourier seasonality transformations available:

- Yearly Fourier: A yearly seasonality with a period of 365.25 days
- Monthly Fourier: A monthly seasonality with a period of 365.25 / 12 days

.. plot::
    :context: close-figs

    import matplotlib.pyplot as plt
    import numpy as np
    import arviz as az
    from pymc_marketing.mmm import YearlyFourier
    from pymc_marketing.prior import Prior

    plt.style.use('arviz-darkgrid')

    prior = Prior(
        "Normal",
        mu=[0, 0, -1, 0],
        sigma=Prior("Gamma", mu=0.10, sigma=0.1, dims="fourier"),
        dims=("hierarchy", "fourier"),
    )
    yearly = YearlyFourier(n_order=2, prior=prior)
    coords = {"hierarchy": ["A", "B"]}
    prior = yearly.sample_prior(coords=coords)
    curve = yearly.sample_curve(prior)
    fig, _ = yearly.plot_curve(curve, subplot_kwargs={"ncols": 1})
    fig.suptitle("Yearly Fourier Seasonality")
    plt.show()

Examples
--------
Use yearly fourier seasonality for custom Marketing Mix Model.

.. code-block:: python

    import pandas as pd
    import pymc as pm

    from pymc_marketing.mmm import YearlyFourier

    yearly = YearlyFourier(n_order=3)

    dates = pd.date_range("2023-01-01", periods=52, freq="W-MON")

    dayofyear = dates.dayofyear.to_numpy()

    with pm.Model() as model:
        fourier_trend = yearly.apply(dayofyear)

Plot the prior fourier seasonality trend.

.. code-block:: python

    import matplotlib.pyplot as plt

    prior = yearly.sample_prior()
    curve = yearly.sample_curve(prior)
    yearly.plot_curve(curve)
    plt.show()

Change the prior distribution of the fourier seasonality.

.. code-block:: python

    from pymc_marketing.mmm import YearlyFourier
    from pymc_marketing.prior import Prior

    prior = Prior("Normal", mu=0, sigma=0.10)
    yearly = YearlyFourier(n_order=6, prior=prior)

Even make it hierarchical...

.. code-block:: python

    from pymc_marketing.mmm import YearlyFourier
    from pymc_marketing.prior import Prior

    # "fourier" is the default prefix!
    prior = Prior(
        "Laplace",
        mu=Prior("Normal", dims="fourier"),
        b=Prior("HalfNormal", sigma=0.1, dims="fourier"),
        dims=("fourier", "hierarchy"),
    )
    yearly = YearlyFourier(n_order=3, prior=prior)

All the plotting will still work! Just pass any coords.

.. code-block:: python

    import matplotlib.pyplot as plt

    coords = {"hierarchy": ["A", "B", "C"]}
    prior = yearly.sample_prior(coords=coords)
    curve = yearly.sample_curve(prior)
    yearly.plot_curve(curve)
    plt.show()

Out of sample predictions with fourier seasonality by changing the day of year
used in the model.

.. code-block:: python

    import pandas as pd
    import pymc as pm

    from pymc_marketing.mmm import YearlyFourier

    periods = 52 * 3
    dates = pd.date_range("2022-01-01", periods=periods, freq="W-MON")

    training_dates = dates[:52 * 2]
    testing_dates = dates[52 * 2:]

    yearly = YearlyFourier(n_order=3)

    coords = {
        "date": training_dates,
    }
    with pm.Model(coords=coords) as model:
        dayofyear = pm.Data(
            "dayofyear",
            training_dates.dayofyear.to_numpy(),
            dims="date",
        )

        trend = pm.Deterministic(
            "trend",
            yearly.apply(dayofyear),
            dims="date",
        )

        idata = pm.sample_prior_predictive().prior

    with model:
        pm.set_data(
            {"dayofyear": testing_dates.dayofyear.to_numpy()},
            coords={"date": testing_dates},
        )

        out_of_sample = pm.sample_posterior_predictive(
            idata,
            var_names=["trend"],
        ).posterior_predictive["trend"]


Use yearly and monthly fourier seasonality together.

By default, the prefix of the fourier seasonality is set to "fourier". However,
the prefix can be changed upon initialization in order to avoid variable name
conflicts.

.. code-block:: python

    import pandas as pd
    import pymc as pm

    from pymc_marketing.mmm import (
        MonthlyFourier,
        YearlyFourier,
    )

    yearly = YearlyFourier(n_order=6, prefix="yearly")
    monthly = MonthlyFourier(n_order=3, prefix="monthly")

    dates = pd.date_range("2023-01-01", periods=52, freq="W-MON")
    dayofyear = dates.dayofyear.to_numpy()

    coords = {
        "date": dates,
    }

    with pm.Model(coords=coords) as model:
        yearly_trend = yearly.apply(dayofyear)
        monthly_trend = monthly.apply(dayofyear)

        trend = pm.Deterministic(
            "trend",
            yearly_trend + monthly_trend,
            dims="date",
        )

    with model:
        prior_samples = pm.sample_prior_predictive().prior

"""

from collections.abc import Callable
from typing import Any

import arviz as az
import matplotlib.pyplot as plt
import numpy as np
import numpy.typing as npt
import pymc as pm
import pytensor.tensor as pt
import xarray as xr

from pymc_marketing.constants import DAYS_IN_MONTH, DAYS_IN_YEAR
from pymc_marketing.mmm.plot import (
    plot_curve,
    plot_hdi,
    plot_samples,
)
from pymc_marketing.prior import Prior, create_dim_handler

X_NAME: str = "day"
NON_GRID_NAMES: frozenset[str] = frozenset({X_NAME})


def generate_fourier_modes(
    periods: pt.TensorLike,
    n_order: int,
) -> pt.TensorVariable:
    """Create fourier modes for a given period.

    Parameters
    ----------
    periods : pt.TensorLike
        Periods to generate fourier modes for.
    n_order : int
        Number of fourier modes to generate.

    Returns
    -------
    pt.TensorVariable
        Fourier modes.

    """
    multiples = pt.arange(1, n_order + 1)
    x = 2 * pt.pi * periods

    values = x[:, None] * multiples

    return pt.concatenate(
        [
            pt.sin(values),
            pt.cos(values),
        ],
        axis=1,
    )


class FourierBase:
    """Base class for Fourier seasonality transformations.

    Parameters
    ----------
    n_order : int
        Number of fourier modes to use.
    prefix : str, optional
        Alternative prefix for the fourier seasonality, by default None or
        "fourier"
    prior : Prior, optional
        Prior distribution for the fourier seasonality beta parameters, by
        default None
    name : str, optional
        Name of the variable that multiplies the fourier modes, by default None

    Attributes
    ----------
    days_in_period : float
        Number of days in a period.
    prefix : str
        Name of model coordinates
    default_prior : Prior
        Default prior distribution for the fourier seasonality
        beta parameters.

    """

    days_in_period: float
    prefix: str = "fourier"

    default_prior = Prior("Laplace", mu=0, b=1)

    def __init__(
        self,
        n_order: int,
        prefix: str | None = None,
        prior: Prior | None = None,
        name: str | None = None,
    ) -> None:
        if not isinstance(n_order, int) or n_order < 1:
            raise ValueError(f"n_order must be a positive integer. Not {n_order}")

        self.n_order = n_order
        self.prefix = prefix or self.prefix
        self.prior = prior or self.default_prior
        self.variable_name = name or f"{self.prefix}_beta"

        if self.variable_name == self.prefix:
            raise ValueError("Variable name cannot be the same as the prefix")

        if not self.prior.dims:
            self.prior = self.prior.deepcopy()
            self.prior.dims = self.prefix

        if self.prefix not in self.prior.dims:
            raise ValueError(f"Prior distribution must have dimension {self.prefix}")

    @property
    def nodes(self) -> list[str]:
        """Fourier node names for model coordinates."""
        return [
            f"{func}_{i}" for func in ["sin", "cos"] for i in range(1, self.n_order + 1)
        ]

    def apply(
        self,
        dayofyear: pt.TensorLike,
        result_callback: Callable[[pt.TensorVariable], None] | None = None,
    ) -> pt.TensorVariable:
        """Apply fourier seasonality to day of year.

        Must be used within a PyMC model context.

        Parameters
        ----------
        dayofyear : pt.TensorLike
            Day of year.
        result_callback : Callable[[pt.TensorVariable], None], optional
            Callback function to apply to the result, by default None

        Returns
        -------
        pt.TensorVariable
            Fourier seasonality

        Examples
        --------
        Save off the result before summing through the prefix dimension.

        .. code-block:: python

            import pandas as pd

            import pymc as pm

            from pymc_marketing.mmm import YearlyFourier

            fourier = YearlyFourier(n_order=3)

            def callback(result):
                pm.Deterministic("fourier_trend", result, dims=("date", "fourier"))

            dates = pd.date_range("2023-01-01", periods=52, freq="W-MON")

            coords = {
                "date": dates,
            }
            with pm.Model(coords=coords) as model:
                dayofyear = dates.dayofyear.to_numpy()
                fourier.apply(dayofyear, result_callback=callback)

        """
        periods = dayofyear / self.days_in_period

        model = pm.modelcontext(None)
        model.add_coord(self.prefix, self.nodes)

        beta = self.prior.create_variable(self.variable_name)

        fourier_modes = generate_fourier_modes(periods=periods, n_order=self.n_order)

        DUMMY_DIM = "DATE"

        prefix_idx = self.prior.dims.index(self.prefix)
        result_dims = (DUMMY_DIM, *self.prior.dims)
        dim_handler = create_dim_handler(result_dims)

        result = dim_handler(fourier_modes, (DUMMY_DIM, self.prefix)) * dim_handler(
            beta, self.prior.dims
        )
        if result_callback is not None:
            result_callback(result)

        return result.sum(axis=prefix_idx + 1)

    def sample_prior(self, coords: dict | None = None, **kwargs) -> xr.Dataset:
        """Sample the prior distributions.

        Parameters
        ----------
        coords : dict, optional
            Coordinates for the prior distribution, by default None
        kwargs
            Additional keywords for sample_prior_predictive

        Returns
        -------
        xr.Dataset
            Prior distribution.

        """
        coords = coords or {}
        coords[self.prefix] = self.nodes
        return self.prior.sample_prior(coords=coords, name=self.variable_name, **kwargs)

    def sample_curve(self, parameters: az.InferenceData | xr.Dataset) -> xr.DataArray:
        """Create full period of the fourier seasonality.

        Parameters
        ----------
        parameters : az.InferenceData | xr.Dataset
            Inference data or dataset containing the fourier parameters.
            Can be posterior or prior.

        Returns
        -------
        xr.DataArray
            Full period of the fourier seasonality.

        """
        full_period = np.arange(self.days_in_period + 1)
        coords = {
            "day": full_period,
        }
        for key, values in parameters[self.variable_name].coords.items():
            if key in {"chain", "draw", self.prefix}:
                continue
            coords[key] = values.to_numpy()

        with pm.Model(coords=coords):
            name = f"{self.prefix}_trend"
            pm.Deterministic(
                name,
                self.apply(dayofyear=full_period),
                dims=tuple(coords.keys()),
            )

            return pm.sample_posterior_predictive(
                parameters,
                var_names=[name],
            ).posterior_predictive[name]

    def plot_curve(
        self,
        curve: xr.DataArray,
        subplot_kwargs: dict | None = None,
        sample_kwargs: dict | None = None,
        hdi_kwargs: dict | None = None,
    ) -> tuple[plt.Figure, npt.NDArray[plt.Axes]]:
        """Plot the seasonality for one full period.

        Parameters
        ----------
        curve : xr.DataArray
            Sampled full period of the fourier seasonality.
        subplot_kwargs : dict, optional
            Keyword arguments for the subplot, by default None
        sample_kwargs : dict, optional
            Keyword arguments for the plot_full_period_samples method, by default None
        hdi_kwargs : dict, optional
            Keyword arguments for the plot_full_period_hdi method, by default None

        Returns
        -------
        tuple[plt.Figure, npt.NDArray[plt.Axes]]
            Matplotlib figure and axes.

        """
        return plot_curve(
            curve,
            non_grid_names=set(NON_GRID_NAMES),
            subplot_kwargs=subplot_kwargs,
            sample_kwargs=sample_kwargs,
            hdi_kwargs=hdi_kwargs,
        )

    def plot_curve_hdi(
        self,
        curve: xr.DataArray,
        hdi_kwargs: dict | None = None,
        subplot_kwargs: dict[str, Any] | None = None,
        plot_kwargs: dict[str, Any] | None = None,
        axes: npt.NDArray[plt.Axes] | None = None,
    ) -> tuple[plt.Figure, npt.NDArray[plt.Axes]]:
        """Plot full period of the fourier seasonality.

        Parameters
        ----------
        curve : xr.DataArray
            The curve to plot.
        hdi_kwargs : dict, optional
            Keyword arguments for the az.hdi function. Defaults to None.
        plot_kwargs : dict, optional
            Keyword arguments for the fill_between function. Defaults to None.
        subplot_kwargs : dict, optional
            Keyword arguments for plt.subplots
        axes : npt.NDArray[plt.Axes], optional
            The exact axes to plot on. Overrides any subplot_kwargs

        Returns
        -------
        tuple[plt.Figure, npt.NDArray[plt.Axes]]

        """
        return plot_hdi(
            curve,
            non_grid_names=set(NON_GRID_NAMES),
            hdi_kwargs=hdi_kwargs,
            subplot_kwargs=subplot_kwargs,
            plot_kwargs=plot_kwargs,
            axes=axes,
        )

    def plot_curve_samples(
        self,
        curve: xr.DataArray,
        n: int = 10,
        rng: np.random.Generator | None = None,
        plot_kwargs: dict[str, Any] | None = None,
        subplot_kwargs: dict[str, Any] | None = None,
        axes: npt.NDArray[plt.Axes] | None = None,
    ) -> tuple[plt.Figure, npt.NDArray[plt.Axes]]:
        """Plot samples from the curve.

        Parameters
        ----------
        curve : xr.DataArray
            Samples from the curve.
        n : int, optional
            Number of samples to plot, by default 10
        rng : np.random.Generator, optional
            Random number generator, by default None
        plot_kwargs : dict, optional
            Keyword arguments for the plot function, by default None
        subplot_kwargs : dict, optional
            Keyword arguments for the subplot, by default None
        axes : npt.NDArray[plt.Axes], optional
            Matplotlib axes, by default None

        Returns
        -------
        tuple[plt.Figure, npt.NDArray[plt.Axes]]
            Matplotlib figure and axes.

        """
        return plot_samples(
            curve,
            non_grid_names=set(NON_GRID_NAMES),
            n=n,
            rng=rng,
            axes=axes,
            subplot_kwargs=subplot_kwargs,
            plot_kwargs=plot_kwargs,
        )


class YearlyFourier(FourierBase):
    """Yearly fourier seasonality.

    .. plot::
        :context: close-figs

        import arviz as az
        import matplotlib.pyplot as plt
        import numpy as np

        from pymc_marketing.mmm import YearlyFourier
        from pymc_marketing.prior import Prior

        az.style.use("arviz-white")

        seed = sum(map(ord, "Yearly"))
        rng = np.random.default_rng(seed)

        mu = np.array([0, 0, -1, 0])
        b = 0.15
        dist = Prior("Laplace", mu=mu, b=b, dims="fourier")
        yearly = YearlyFourier(n_order=2, prior=dist)
        prior = yearly.sample_prior(random_seed=rng)
        curve = yearly.sample_full_period(prior)

        _, axes = yearly.plot_full_period(curve)
        axes[0].set(title="Yearly Fourier Seasonality")
        plt.show()

    Parameters
    ----------
    n_order : int
        Number of fourier modes to use.
    prefix : str, optional
        Alternative prefix for the fourier seasonality, by default None or
        "fourier"
    prior : Prior, optional
        Prior distribution for the fourier seasonality beta parameters, by
        default None

    Attributes
    ----------
    days_in_period : float
        Number of days in a period.
    prefix : str
        Name of model coordinates
    default_prior : Prior
        Default prior distribution for the fourier seasonality
        beta parameters.

    """

    days_in_period = DAYS_IN_YEAR


class MonthlyFourier(FourierBase):
    """Monthly fourier seasonality.

    .. plot::
        :context: close-figs

        import arviz as az
        import matplotlib.pyplot as plt
        import numpy as np

        from pymc_marketing.mmm import MonthlyFourier
        from pymc_marketing.prior import Prior

        az.style.use("arviz-white")

        seed = sum(map(ord, "Monthly"))
        rng = np.random.default_rng(seed)

        mu = np.array([0, 0, 0.5, 0])
        b = 0.075
        dist = Prior("Laplace", mu=mu, b=b, dims="fourier")
        yearly = MonthlyFourier(n_order=2, prior=dist)
        prior = yearly.sample_prior(samples=100)
        curve = yearly.sample_full_period(prior)

        _, axes = yearly.plot_full_period(curve)
        axes[0].set(title="Monthly Fourier Seasonality")
        plt.show()

    Parameters
    ----------
    n_order : int
        Number of fourier modes to use.
    prefix : str, optional
        Alternative prefix for the fourier seasonality, by default None or
        "fourier"
    prior : Prior, optional
        Prior distribution for the fourier seasonality beta parameters, by
        default None

    Attributes
    ----------
    days_in_period : float
        Number of days in a period.
    prefix : str
        Name of model coordinates
    default_prior : Prior
        Default prior distribution for the fourier seasonality
        beta parameters.

    """

    days_in_period = DAYS_IN_MONTH
