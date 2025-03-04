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
import matplotlib.pyplot as plt
import numpy as np
import pymc as pm
import pytest
import xarray as xr

from pymc_marketing.mmm.fourier import YearlyFourier, generate_fourier_modes
from pymc_marketing.prior import Prior


def test_prior_without_dims() -> None:
    prior = Prior("Normal")
    yearly = YearlyFourier(n_order=2, prior=prior)

    assert yearly.prior.dims == (yearly.prefix,)
    assert prior.dims == ()


def test_prior_doesnt_have_prefix() -> None:
    prior = Prior("Normal", dims="hierarchy")
    with pytest.raises(ValueError, match="Prior distribution must have"):
        YearlyFourier(n_order=2, prior=prior)


def test_nodes() -> None:
    yearly = YearlyFourier(n_order=2)

    assert yearly.nodes == ["sin_1", "sin_2", "cos_1", "cos_2"]


def test_sample_prior() -> None:
    n_order = 2
    yearly = YearlyFourier(n_order=n_order)
    prior = yearly.sample_prior(samples=10)

    assert prior.sizes == {
        "chain": 1,
        "draw": 10,
        yearly.prefix: n_order * 2,
    }


def test_sample_curve() -> None:
    n_order = 2
    yearly = YearlyFourier(n_order=n_order)
    prior = yearly.sample_prior(samples=10)
    curve = yearly.sample_curve(prior)

    assert curve.sizes == {
        "chain": 1,
        "draw": 10,
        "day": 367,
    }


def create_mock_variable(coords):
    shape = [len(values) for values in coords.values()]

    return xr.DataArray(
        np.ones(shape),
        coords=coords,
    )


@pytest.fixture
def mock_parameters() -> xr.Dataset:
    n_chains = 1
    n_draws = 250

    return xr.Dataset(
        {
            "fourier_beta": create_mock_variable(
                coords={
                    "chain": np.arange(n_chains),
                    "draw": np.arange(n_draws),
                    "fourier": ["sin_1", "sin_2", "cos_1", "cos_2"],
                }
            ).rename("fourier_beta"),
            "another_larger_variable": create_mock_variable(
                coords={
                    "chain": np.arange(n_chains),
                    "draw": np.arange(n_draws),
                    "additional_dim": np.arange(10),
                }
            ).rename("another_larger_variable"),
        },
    )


def test_sample_curve_additional_dims(mock_parameters) -> None:
    yearly = YearlyFourier(n_order=2)
    curve = yearly.sample_curve(mock_parameters)

    assert curve.sizes == {
        "chain": 1,
        "draw": 250,
        "day": 367,
    }


def test_additional_dimension() -> None:
    prior = Prior("Normal", dims=("fourier", "additional_dim", "yet_another_dim"))
    yearly = YearlyFourier(n_order=2, prior=prior)

    coords = {
        "additional_dim": range(2),
        "yet_another_dim": range(3),
    }
    prior = yearly.sample_prior(samples=10, coords=coords)
    curve = yearly.sample_curve(prior)

    assert curve.sizes == {
        "chain": 1,
        "draw": 10,
        "additional_dim": 2,
        "yet_another_dim": 3,
        "day": 367,
    }


def test_plot_curve() -> None:
    prior = Prior("Normal", dims=("fourier", "additional_dim"))
    yearly = YearlyFourier(n_order=2, prior=prior)

    coords = {"additional_dim": range(4)}
    prior = yearly.sample_prior(samples=10, coords=coords)
    curve = yearly.sample_curve(prior)

    subplot_kwargs = {"ncols": 2}
    fig, axes = yearly.plot_curve(curve, subplot_kwargs=subplot_kwargs)

    assert isinstance(fig, plt.Figure)
    assert axes.shape == (2, 2)


@pytest.mark.parametrize("n_order", [0, -1, -100, 2.5])
def test_bad_order(n_order) -> None:
    with pytest.raises(ValueError, match="n_order must be a positive integer"):
        YearlyFourier(n_order=n_order)


@pytest.mark.parametrize(
    "periods, n_order, expected_shape",
    [
        (np.linspace(start=0.0, stop=1.0, num=50), 10, (50, 10 * 2)),
        (np.linspace(start=-1.0, stop=1.0, num=70), 9, (70, 9 * 2)),
        (np.ones(shape=1), 1, (1, 1 * 2)),
    ],
)
def test_fourier_modes_shape(periods, n_order, expected_shape) -> None:
    result = generate_fourier_modes(periods, n_order)
    assert result.eval().shape == expected_shape


@pytest.mark.parametrize(
    "periods, n_order",
    [
        (np.linspace(start=0.0, stop=1.0, num=50), 10),
        (np.linspace(start=-1.0, stop=1.0, num=70), 9),
        (np.ones(shape=1), 1),
    ],
)
def test_fourier_modes_range(periods, n_order):
    fourier_modes = generate_fourier_modes(periods=periods, n_order=n_order).eval()

    assert fourier_modes.min() >= -1.0
    assert fourier_modes.max() <= 1.0


@pytest.mark.parametrize(
    "periods, n_order",
    [
        (np.linspace(start=-1.0, stop=1.0, num=100), 10),
        (np.linspace(start=-10.0, stop=2.0, num=170), 60),
        (np.linspace(start=-15, stop=5.0, num=160), 20),
    ],
)
def test_fourier_modes_frequency_integer_range(periods, n_order):
    fourier_modes = generate_fourier_modes(periods=periods, n_order=n_order).eval()

    assert (fourier_modes[:, :n_order].mean(axis=0) < 1e-10).all()
    assert (fourier_modes[:-1, n_order:].mean(axis=0) < 1e-10).all()

    assert fourier_modes[fourier_modes > 0].shape
    assert fourier_modes[fourier_modes < 0].shape
    assert fourier_modes[fourier_modes == 0].shape
    assert fourier_modes[fourier_modes == 1].shape


@pytest.mark.parametrize(
    "periods, n_order",
    [
        (np.linspace(start=0.0, stop=1.0, num=100), 10),
        (np.linspace(start=0.0, stop=2.0, num=170), 60),
        (np.linspace(start=0.0, stop=5.0, num=160), 20),
        (np.linspace(start=-9.0, stop=1.0, num=100), 10),
        (np.linspace(start=-80.0, stop=2.0, num=170), 60),
        (np.linspace(start=-100.0, stop=-5.0, num=160), 20),
    ],
)
def test_fourier_modes_pythagoras(periods, n_order):
    fourier_modes = generate_fourier_modes(periods=periods, n_order=n_order).eval()
    norm = fourier_modes[:, :n_order] ** 2 + fourier_modes[:, n_order:] ** 2

    assert (abs(norm - 1) < 1e-10).all()


def test_apply_result_callback() -> None:
    n_order = 3
    fourier = YearlyFourier(n_order=n_order)

    def result_callback(x):
        pm.Deterministic(
            "components",
            x,
            dims=("dayofyear", *fourier.prior.dims),
        )

    dayofyear = np.arange(365)
    coords = {
        "dayofyear": dayofyear,
    }
    with pm.Model(coords=coords) as model:
        fourier.apply(dayofyear, result_callback=result_callback)

    assert "components" in model
    assert model["components"].eval().shape == (365, n_order * 2)


def test_error_with_prefix_and_name() -> None:
    name = "variable_name"
    with pytest.raises(ValueError, match="Variable name cannot"):
        YearlyFourier(n_order=2, name=name, prefix=name)


def test_change_name() -> None:
    variable_name = "variable_name"
    fourier = YearlyFourier(n_order=2, name=variable_name)
    assert fourier.variable_name == variable_name
    prior = fourier.sample_prior(samples=10)
    assert variable_name in prior
