#!/usr/bin/env python
# -*- coding: utf-8 -*-

# This file is part of the
#   GriSPy Project (https://github.com/mchalela/GriSPy).
# Copyright (c) 2019, Martin Chalela
# License: MIT
#   Full Text: https://github.com/mchalela/GriSPy/blob/master/LICENSE


# =============================================================================
# DOCS
# =============================================================================

"""GriSPy core class."""

# =============================================================================
# IMPORTS
# =============================================================================

import time
import datetime
from collections import namedtuple

import numpy as np

import attr

from . import utils, distances


# =============================================================================
# CONSTANTS
# =============================================================================

METRICS = {
    "euclid": distances.euclid,
    "haversine": distances.haversine,
    "vincenty": distances.vincenty}


EMPTY_ARRAY = np.array([], dtype=int)


# =============================================================================
#  TIME CLASS
# =============================================================================

BuildStats = namedtuple("BuildStats", ["buildtime", "datetime"])


# =============================================================================
# MAIN CLASS
# =============================================================================

@attr.s
class GriSPy(object):
    """Grid Search in Python.

    GriSPy is a regular grid search algorithm for quick nearest-neighbor
    lookup.

    This class indexes a set of k-dimensional points in a regular grid
    providing a fast aproach for nearest neighbors queries. Optional periodic
    boundary conditions can be provided for each axis individually.

    The algorithm has the following queries implemented:
    - bubble_neighbors: find neighbors within a given radius. A different
    radius for each centre can be provided.
    - shell_neighbors: find neighbors within given lower and upper radius.
    Different lower and upper radius can be provided for each centre.
    - nearest_neighbors: find the nth nearest neighbors for each centre.

    Other methods:
    - set_periodicity: set periodicity condition after the grid was built.
    - save_grid: save the grid for future use.
    - load_grid: load a grid previously saved.

    To be implemented:
    - box_neighbors: find neighbors within a k-dimensional squared box of
    a given size and orientation.
    - n_jobs: number of cores for parallel computation.

    Parameters
    ----------
    data: ndarray, shape(n,k)
        The n data points of dimension k to be indexed. This array is not
        copied, and so modifying this data may result in erroneous results.
        The data can be copied if the grid is built with copy_data=True.
    N_cells: positive int, optional
        The number of cells of each dimension to build the grid. The final
        grid will have N_cells**k number of cells. Default: 20
    copy_data: bool, optional
        Flag to indicate if the data should be copied in memory.
        Default: False
    periodic: dict, optional
        Dictionary indicating if the data domain is periodic in some or all its
        dimensions. The key is an integer that correspond to the number of
        dimensions in data, going from 0 to k-1. The value is a tuple with the
        domain limits and the data must be contained within these limits. If an
        axis is not specified, or if its value is None, it will be considered
        as non-periodic. Important: The periodicity only works within one
        periodic range. Default: all axis set to None.
        Example, periodic = { 0: (0, 360), 1: None}.
    metric: str, optional
        Metric definition to compute distances. Options: 'euclid', 'haversine'
        'vincenty' or a custom callable.


    Attributes
    ----------
    dim: int
        The dimension of a single data-point.
    grid_: dict
        This dictionary contains the data indexed in a grid. The key is a
        tuple with the k-dimensional index of each grid cell. Empty cells
        do not have a key. The value is a list of data points indices which
        are located within the given cell.
    k_bins: ndarray, shape (N_cells+1,k)
        The limits of the grid cells in each dimension.
    periodic_flag_: bool
        If any dimension has periodicity.
    time_: grispy.core.BuildStats
        Object containing the building time and the date of build.

    """

    # User input params
    data = attr.ib(default=None, kw_only=False, repr=False)
    N_cells = attr.ib(default=20)
    periodic = attr.ib(factory=dict)  # The validator runs in set_periodicity()
    metric = attr.ib(default="euclid")
    copy_data = attr.ib(
        default=False, validator=attr.validators.instance_of(bool))

    # params
    dim_ = attr.ib(init=False, repr=False)
    grid_ = attr.ib(init=False, repr=False)
    k_bins_ = attr.ib(init=False, repr=False)
    periodic_flag_ = attr.ib(init=False, repr=False)
    time_ = attr.ib(init=False, repr=False)

    # =========================================================================
    # ATTRS INITIALIZATION
    # =========================================================================

    def __attrs_post_init__(self):
        """Init more params and build the grid."""
        t0 = time.time()

        if self.copy_data:
            self.data = self.data.copy()
        self.dim_ = self.data.shape[1]
        self.periodic_flag_ = self._set_periodicity(self.periodic)
        self.grid_, self.k_bins_ = self._build_grid()

        # Record date and build time
        self.time_ = BuildStats(
            buildtime=time.time() - t0,
            datetime=datetime.datetime.now())

    @data.validator
    def _validate_data(self, attribute, value):
        """Validate init params: data."""
        # Chek if numpy array
        if not isinstance(value, np.ndarray):
            raise TypeError(
                "Data: Argument must be a numpy array."
                "Got instead type {}".format(type(value))
            )
        # Check if data has the expected dimension
        if value.ndim != 2:
            raise ValueError(
                "Data: Array has the wrong shape. Expected shape of (n, k), "
                "got instead {}".format(value.shape)
            )
        # Check if data has the expected dimension
        if len(value.flatten()) == 0:
            raise ValueError("Data: Array must have at least 1 point")

        # Check if every data point is valid
        if not np.isfinite(value).all():
            raise ValueError("Data: Array must have real numbers")

    @N_cells.validator
    def _validate_N_cells(self, attr, value):
        """Validate init params: N_cells."""
        # Chek if int
        if not isinstance(value, int):
            raise TypeError(
                "N_cells: Argument must be an integer. "
                "Got instead type {}".format(type(value))
            )
        # Check if N_cells is valid, i.e. higher than 1
        if value < 1:
            raise ValueError(
                "N_cells: Argument must be higher than 1. "
                "Got instead {}".format(value)
            )

    @metric.validator
    def _validate_metric(self, attr, value):
        """Validate init params: metric."""
        # Check if name is valid
        if value not in METRICS and not callable(value):
            metric_names = ", ".join(METRICS)
            raise ValueError(
                "Metric: Got an invalid name: '{}'. "
                "Options are: {} or a callable".format(value, metric_names))

    # =========================================================================
    # INTERNAL IMPLEMENTATION
    # =========================================================================

    def __getitem__(self, key):
        """Get item."""
        return getattr(self, key)

    def _digitize(self, data, bins):
        """Return data bin index."""
        N = len(bins) - 1
        d = (N * (data - bins[0]) / (bins[-1] - bins[0])).astype(np.int)
        return d

    def _build_grid(self, epsilon=1.0e-6):
        """Build the grid."""
        data_ind = np.arange(len(self.data))
        k_bins = np.zeros((self.N_cells + 1, self.dim_))
        k_digit = np.zeros(self.data.shape, dtype=int)
        for k in range(self.dim_):
            k_data = self.data[:, k]
            k_bins[:, k] = np.linspace(
                k_data.min() - epsilon,
                k_data.max() + epsilon,
                self.N_cells + 1,
            )
            k_digit[:, k] = self._digitize(k_data, bins=k_bins[:, k])

        # Check that there is at least one point per cell
        grid = {}
        if self.N_cells ** self.dim_ < len(self.data):
            compact_ind = np.ravel_multi_index(
                k_digit.T,
                (self.N_cells,) * self.dim_,
                order="F",
            )

            compact_ind_sort = np.argsort(compact_ind)
            compact_ind = compact_ind[compact_ind_sort]
            k_digit = k_digit[compact_ind_sort]

            split_ind = np.searchsorted(
                compact_ind, np.arange(self.N_cells ** self.dim_))
            deleted_cells = np.diff(np.append(-1, split_ind)).astype(bool)
            split_ind = split_ind[deleted_cells]
            if split_ind[-1] > data_ind[-1]:
                split_ind = split_ind[:-1]

            list_ind = np.split(data_ind[compact_ind_sort], split_ind[1:])
            k_digit = k_digit[split_ind]

            for i, j in enumerate(k_digit):
                grid[tuple(j)] = tuple(list_ind[i])
        else:
            for i in range(len(self.data)):
                cell_point = tuple(k_digit[i, :])
                if cell_point not in grid:
                    grid[cell_point] = [i]
                else:
                    grid[cell_point].append(i)
        return grid, k_bins

    def _distance(self, centre_0, centres):
        """Compute distance between points.

        metric options: 'euclid', 'sphere'

        Notes: In the case of 'sphere' metric, the input units must be degrees.

        """
        if len(centres) == 0:
            return EMPTY_ARRAY.copy()
        metric_func = (
            self.metric if callable(self.metric) else METRICS[self.metric])
        return metric_func(centre_0, centres, self.dim_)

    def _get_neighbor_distance(self, centres, neighbor_cells):
        """Retrieve neighbor distances whithin the given cells."""
        neighbors_indices = []
        neighbors_distances = []
        for i in range(len(centres)):
            if len(neighbor_cells[i]) == 0:  # no hay celdas vecinas
                neighbors_indices += [EMPTY_ARRAY.copy()]
                neighbors_distances += [EMPTY_ARRAY.copy()]
                continue
            # Genera una lista con los vecinos de cada celda
            # print neighbor_cells[i]
            ind_tmp = [
                self.grid_.get(tuple(neighbor_cells[i][j]), [])
                for j in range(len(neighbor_cells[i]))
            ]
            # Une en una sola lista todos sus vecinos
            neighbors_indices += [np.concatenate(ind_tmp).astype(int)]
            neighbors_distances += [
                self._distance(centres[i], self.data[neighbors_indices[i], :])
            ]
        return neighbors_distances, neighbors_indices

    # Neighbor-cells methods
    def _get_neighbor_cells(
        self,
        centres,
        distance_upper_bound,
        distance_lower_bound=0,
        shell_flag=False,
    ):
        """Retrieve cells touched by the search radius."""
        cell_point = np.zeros((len(centres), self.dim_), dtype=int)
        out_of_field = np.zeros(len(cell_point), dtype=bool)
        for k in range(self.dim_):
            cell_point[:, k] = (
                self._digitize(centres[:, k], bins=self.k_bins_[:, k])
            )
            out_of_field[
                (centres[:, k] - distance_upper_bound > self.k_bins_[-1, k])
            ] = True
            out_of_field[
                (centres[:, k] + distance_upper_bound < self.k_bins_[0, k])
            ] = True

        if np.all(out_of_field):
            # no neighbor cells
            return [EMPTY_ARRAY.copy() for _ in centres]

        # Armo la caja con celdas a explorar
        k_cell_min = np.zeros((len(centres), self.dim_), dtype=int)
        k_cell_max = np.zeros((len(centres), self.dim_), dtype=int)
        for k in range(self.dim_):
            k_cell_min[:, k] = (
                self._digitize(
                    centres[:, k] - distance_upper_bound,
                    bins=self.k_bins_[:, k],
                )
            )
            k_cell_max[:, k] = (
                self._digitize(
                    centres[:, k] + distance_upper_bound,
                    bins=self.k_bins_[:, k],
                )
            )

            k_cell_min[k_cell_min[:, k] < 0, k] = 0
            k_cell_max[k_cell_max[:, k] < 0, k] = 0
            k_cell_min[k_cell_min[:, k] >= self.N_cells, k] = self.N_cells - 1
            k_cell_max[k_cell_max[:, k] >= self.N_cells, k] = self.N_cells - 1

        cell_size = self.k_bins_[1, :] - self.k_bins_[0, :]
        cell_radii = 0.5 * np.sum(cell_size ** 2) ** 0.5

        neighbor_cells = []
        for i in range(len(centres)):
            # Para cada centro i, agrego un arreglo con shape (:,k)
            k_grids = [
                np.arange(k_cell_min[i, k], k_cell_max[i, k] + 1)
                for k in range(self.dim_)
            ]
            k_grids = np.meshgrid(*k_grids)
            neighbor_cells += [
                np.array(list(map(np.ndarray.flatten, k_grids))).T
            ]

            # Calculo la distancia de cada centro i a sus celdas vecinas,
            # luego descarto las celdas que no toca el circulo definido por
            # la distancia
            cells_physical = [
                self.k_bins_[neighbor_cells[i][:, k], k] + 0.5 * cell_size[k]
                for k in range(self.dim_)
            ]
            cells_physical = np.array(cells_physical).T
            mask_cells = (
                self._distance(
                    centres[i], cells_physical
                ) < distance_upper_bound[i] + cell_radii
            )

            if shell_flag:
                mask_cells *= (
                    self._distance(
                        centres[i], cells_physical
                    ) > distance_lower_bound[i] - cell_radii
                )

            if np.any(mask_cells):
                neighbor_cells[i] = neighbor_cells[i][mask_cells]
            else:
                neighbor_cells[i] = EMPTY_ARRAY.copy()
        return neighbor_cells

    def _near_boundary(self, centres, distance_upper_bound):
        mask = np.zeros((len(centres), self.dim_), dtype=bool)
        for k in range(self.dim_):
            if self.periodic[k] is None:
                continue
            mask[:, k] = abs(
                centres[:, k] - self.periodic[k][0]
            ) < distance_upper_bound
            mask[:, k] += abs(
                centres[:, k] - self.periodic[k][1]
            ) < distance_upper_bound
        return mask.sum(axis=1, dtype=bool)

    def _mirror(self, centre, distance_upper_bound):
        mirror_centre = centre - self._periodic_edges
        mask = self._periodic_direc * distance_upper_bound
        mask += mirror_centre
        mask = (mask >= self._pd_low) * (mask <= self._pd_hi)
        mask = np.prod(mask, 1, dtype=bool)
        return mirror_centre[mask]

    def _mirror_universe(self, centres, distance_upper_bound):
        """Generate Terran centres in the Mirror Universe."""
        terran_centres = np.array([[]] * self.dim_).T
        terran_indices = np.array([], dtype=int)
        near_boundary = self._near_boundary(centres, distance_upper_bound)
        if not np.any(near_boundary):
            return terran_centres, terran_indices

        for i, centre in enumerate(centres):
            if not near_boundary[i]:
                continue
            mirror_centre = self._mirror(centre, distance_upper_bound[i])
            if len(mirror_centre) > 0:
                terran_centres = np.concatenate(
                    (terran_centres, mirror_centre), axis=0
                )
                terran_indices = np.concatenate(
                    (terran_indices, np.repeat(i, len(mirror_centre)))
                )
        return terran_centres, terran_indices

    def _set_periodicity(self, periodic={}):
        """Set periodicity conditions.

        This allows to define or change the periodicity limits without
        having to construct the grid again.

        Important: The periodicity only works within one periodic range.

        Parameters
        ----------
        periodic: dict, optional
            Dictionary indicating if the data domain is periodic in some or all
            its dimensions. The key is an integer that corresponds to the
            number of dimensions in data, going from 0 to k-1. The value is a
            tuple with the domain limits and the data must be contained within
            these limits. If an axis is not specified, or if its value is None,
            it will be considered as non-periodic.
            Default: all axis set to None.
            Example, periodic = { 0: (0, 360), 1: None}.

        """
        # Validate input
        utils.validate_periodicity(periodic)

        self.periodic = {}
        if len(periodic) == 0:
            periodic_flag = False
        else:
            periodic_flag = any(
                [x is not None for x in list(periodic.values())]
            )

            if periodic_flag:

                self._pd_hi = np.ones((1, self.dim_)) * np.inf
                self._pd_low = np.ones((1, self.dim_)) * -np.inf
                self._periodic_edges = []
                for k in range(self.dim_):
                    aux = periodic.get(k)
                    self.periodic[k] = aux
                    if aux:
                        self._pd_low[0, k] = aux[0]
                        self._pd_hi[0, k] = aux[1]
                        aux = np.insert(aux, 1, 0.)
                    else:
                        aux = np.zeros((1, 3))
                    self._periodic_edges = np.hstack([
                        self._periodic_edges,
                        np.tile(aux, (3**(self.dim_ - 1 - k), 3**k)).T.ravel()
                    ])

                self._periodic_edges = self._periodic_edges.reshape(
                    self.dim_, 3**self.dim_
                ).T
                self._periodic_edges -= self._periodic_edges[::-1]
                self._periodic_edges = np.unique(self._periodic_edges, axis=0)
                mask = self._periodic_edges.sum(axis=1, dtype=bool)
                self._periodic_edges = self._periodic_edges[mask]
                self._periodic_direc = np.sign(self._periodic_edges)

        return periodic_flag

    # =========================================================================
    # API
    # =========================================================================

    def bubble_neighbors(
        self,
        centres,
        distance_upper_bound=-1.0,
        sorted=False,
        kind="quicksort",
    ):
        """Find all points within given distances of each centre.

        Parameters
        ----------
        centres: ndarray, shape (m,k)
            The point or points to search for neighbors of.
        distance_upper_bound: scalar or ndarray of length m
            The radius of points to return. If a scalar is provided, the same
            distance will apply for every centre. An ndarray with individual
            distances can also be rovided.
        sorted: bool, optional
            If True the returned neighbors will be ordered by increasing
            distance to the centre. Default: False.
        kind: str, optional
            When sorted = True, the sorting algorithm can be specified in this
            keyword. Available algorithms are: ['quicksort', 'mergesort',
            'heapsort', 'stable']. Default: 'quicksort'
        njobs: int, optional
            Number of jobs for parallel computation. Not implemented yet.

        Returns
        -------
        distances: list, length m
            Returns a list of m arrays. Each array has the distances to the
            neighbors of that centre.

        indices: list, length m
            Returns a list of m arrays. Each array has the indices to the
            neighbors of that centre.

        """
        # Validate iputs
        utils.validate_centres(centres, self.data)
        utils.validate_distance_bound(distance_upper_bound, self.periodic)
        utils.validate_bool(sorted)
        utils.validate_sortkind(kind)
        # Match distance_upper_bound shape with centres shape
        if np.isscalar(distance_upper_bound):
            distance_upper_bound *= np.ones(len(centres))
        else:
            utils.validate_equalsize(centres, distance_upper_bound)

        # Get neighbors
        neighbor_cells = self._get_neighbor_cells(
            centres, distance_upper_bound
        )
        neighbors_distances, neighbors_indices = self._get_neighbor_distance(
            centres, neighbor_cells
        )

        # We need to generate mirror centres for periodic boundaries...
        if self.periodic_flag_:
            terran_centres, terran_indices = self._mirror_universe(
                centres, distance_upper_bound
            )
            # terran_centres are the centres in the mirror universe for those
            # near the boundary.
            terran_neighbor_cells = self._get_neighbor_cells(
                terran_centres, distance_upper_bound[terran_indices]
            )
            terran_neighbors_distances, \
                terran_neighbors_indices = self._get_neighbor_distance(
                    terran_centres, terran_neighbor_cells
                )

            for i, t in zip(terran_indices, np.arange(len(terran_centres))):
                # i runs over normal indices that have a terran counterpart
                # t runs over terran indices, 0 to len(terran_centres)
                neighbors_distances[i] = np.concatenate(
                    (neighbors_distances[i], terran_neighbors_distances[t])
                )
                neighbors_indices[i] = np.concatenate(
                    (neighbors_indices[i], terran_neighbors_indices[t])
                )

        for i in range(len(centres)):
            mask_distances = neighbors_distances[i] <= distance_upper_bound[i]
            neighbors_distances[i] = neighbors_distances[i][mask_distances]
            neighbors_indices[i] = neighbors_indices[i][mask_distances]
            if sorted:
                sorted_ind = np.argsort(neighbors_distances[i], kind=kind)
                neighbors_distances[i] = neighbors_distances[i][sorted_ind]
                neighbors_indices[i] = neighbors_indices[i][sorted_ind]
        return neighbors_distances, neighbors_indices

    def shell_neighbors(
        self,
        centres,
        distance_lower_bound=-1.0,
        distance_upper_bound=-1.0,
        sorted=False,
        kind="quicksort",
    ):
        """Find all points within given lower and upper distances of each centre.

        Parameters
        ----------
        centres: ndarray, shape (m,k)
            The point or points to search for neighbors of.
        distance_lower_bound: scalar or ndarray of length m
            The minimum distance of points to return. If a scalar is provided,
            the same distance will apply for every centre. An ndarray with
            individual distances can also be rovided.
        distance_upper_bound: scalar or ndarray of length m
            The maximum distance of points to return. If a scalar is provided,
            the same distance will apply for every centre. An ndarray with
            individual distances can also be rovided.
        sorted: bool, optional
            If True the returned neighbors will be ordered by increasing
            distance to the centre. Default: False.
        kind: str, optional
            When sorted = True, the sorting algorithm can be specified in this
            keyword. Available algorithms are: ['quicksort', 'mergesort',
            'heapsort', 'stable']. Default: 'quicksort'
        njobs: int, optional
            Number of jobs for parallel computation. Not implemented yet.

        Returns
        -------
        distances: list, length m
            Returns a list of m arrays. Each array has the distances to the
            neighbors of that centre.

        indices: list, length m
            Returns a list of m arrays. Each array has the indices to the
            neighbors of that centre.

        """
        # Validate inputs
        utils.validate_centres(centres, self.data)
        utils.validate_bool(sorted)
        utils.validate_sortkind(kind)
        utils.validate_shell_distances(
            distance_lower_bound, distance_upper_bound, self.periodic,
        )

        # Match distance bounds shapes with centres shape
        if np.isscalar(distance_lower_bound):
            distance_lower_bound *= np.ones(len(centres))
        else:
            utils.validate_equalsize(centres, distance_lower_bound)
        if np.isscalar(distance_upper_bound):
            distance_upper_bound *= np.ones(len(centres))
        else:
            utils.validate_equalsize(centres, distance_upper_bound)

        # Get neighbors
        neighbor_cells = self._get_neighbor_cells(
            centres,
            distance_upper_bound=distance_upper_bound,
            distance_lower_bound=distance_lower_bound,
            shell_flag=True,
        )
        neighbors_distances, neighbors_indices = self._get_neighbor_distance(
            centres, neighbor_cells
        )

        # We need to generate mirror centres for periodic boundaries...
        if self.periodic_flag_:
            terran_centres, terran_indices = self._mirror_universe(
                centres, distance_upper_bound
            )
            # terran_centres are the centres in the mirror universe for those
            # near the boundary.
            terran_neighbor_cells = self._get_neighbor_cells(
                terran_centres, distance_upper_bound[terran_indices]
            )
            terran_neighbors_distances,\
                terran_neighbors_indices = self._get_neighbor_distance(
                    terran_centres, terran_neighbor_cells
                )

            for i, t in zip(terran_indices, np.arange(len(terran_centres))):
                # i runs over normal indices that have a terran counterpart
                # t runs over terran indices, 0 to len(terran_centres)
                neighbors_distances[i] = np.concatenate(
                    (neighbors_distances[i], terran_neighbors_distances[t])
                )
                neighbors_indices[i] = np.concatenate(
                    (neighbors_indices[i], terran_neighbors_indices[t])
                )

        for i in range(len(centres)):
            mask_distances_upper = (
                neighbors_distances[i] <= distance_upper_bound[i]
            )
            mask_distances_lower = neighbors_distances[i][mask_distances_upper]
            mask_distances_lower = (
                mask_distances_lower > distance_lower_bound[i]
            )
            aux = neighbors_distances[i]
            aux = aux[mask_distances_upper]
            aux = aux[mask_distances_lower]
            neighbors_distances[i] = aux

            aux = neighbors_indices[i]
            aux = aux[mask_distances_upper]
            aux = aux[mask_distances_lower]
            neighbors_indices[i] = aux

            if sorted:
                sorted_ind = np.argsort(neighbors_distances[i], kind=kind)
                neighbors_distances[i] = neighbors_distances[i][sorted_ind]
                neighbors_indices[i] = neighbors_indices[i][sorted_ind]

        return neighbors_distances, neighbors_indices

    def nearest_neighbors(self, centres, n=1, kind="quicksort"):
        """Find the n nearest-neighbors for each centre.

        Parameters
        ----------
        centres: ndarray, shape (m,k)
            The point or points to search for neighbors of.
        n: int, optional
            The number of neighbors to fetch for each centre. Default: 1.
        kind: str, optional
            The returned neighbors will be ordered by increasing distance
            to the centre. The sorting algorithm can be specified in this
            keyword. Available algorithms are: ['quicksort', 'mergesort',
            'heapsort', 'stable']. Default: 'quicksort'
        njobs: int, optional
            Number of jobs for parallel computation. Not implemented yet.

        Returns
        -------
        distances: list, length m
            Returns a list of m arrays. Each array has the distances to the
            neighbors of that centre.

        indices: list, length m
            Returns a list of m arrays. Each array has the indices to the
            neighbors of that centre.

        """
        # Validate input
        utils.validate_centres(centres, self.data)
        utils.validate_n_nearest(n, self.data, self.periodic)
        utils.validate_sortkind(kind)

        # Initial definitions
        N_centres = len(centres)
        centres_lookup_ind = np.arange(0, N_centres)
        n_found = np.zeros(N_centres, dtype=bool)
        lower_distance_tmp = np.zeros(N_centres)
        upper_distance_tmp = np.zeros(N_centres)

        # Abro la celda del centro como primer paso
        centre_cell = self._get_neighbor_cells(
            centres, distance_upper_bound=upper_distance_tmp
        )
        # crear funcion que regrese vecinos sin calcular distancias
        neighbors_distances, neighbors_indices = self._get_neighbor_distance(
            centres, centre_cell
        )

        # Calculo una primera aproximacion con la
        # 'distancia media' = 0.5 * (n/denstiy)**(1/dim)
        # Factor de escala para la distancia inicial
        mean_distance_factor = 1.0
        cell_size = self.k_bins_[1, :] - self.k_bins_[0, :]
        cell_volume = np.prod(cell_size.astype(float))
        neighbors_number = np.array(list(map(len, neighbors_indices)))
        mask_zero_neighbors = neighbors_number == 0
        neighbors_number[mask_zero_neighbors] = 1
        mean_distance = 0.5 * (n / (neighbors_number / cell_volume)) ** (
            1.0 / self.dim_)

        upper_distance_tmp = mean_distance_factor * mean_distance

        neighbors_indices = [EMPTY_ARRAY.copy() for _ in range(N_centres)]
        neighbors_distances = [EMPTY_ARRAY.copy() for _ in range(N_centres)]
        while not np.all(n_found):
            neighbors_distances_tmp,\
                neighbors_indices_tmp = self.shell_neighbors(
                    centres[~n_found],
                    distance_lower_bound=lower_distance_tmp[~n_found],
                    distance_upper_bound=upper_distance_tmp[~n_found],
                )

            for i_tmp, i in enumerate(centres_lookup_ind[~n_found]):
                if n_found[i]:
                    continue
                if n <= len(neighbors_indices_tmp[i_tmp]) + len(
                    neighbors_indices[i]
                ):
                    n_more = n - len(neighbors_indices[i])
                    n_found[i] = True
                else:
                    n_more = len(neighbors_indices_tmp[i_tmp])
                    lower_distance_tmp[i_tmp] = upper_distance_tmp[
                        i_tmp
                    ].copy()
                    upper_distance_tmp[i_tmp] += cell_size.min()

                sorted_ind = np.argsort(
                    neighbors_distances_tmp[i_tmp], kind=kind
                )[:n_more]
                neighbors_distances[i] = np.hstack(
                    (
                        neighbors_distances[i],
                        neighbors_distances_tmp[i_tmp][sorted_ind],
                    )
                )
                neighbors_indices[i] = np.hstack(
                    (
                        neighbors_indices[i],
                        neighbors_indices_tmp[i_tmp][sorted_ind],
                    )
                )

        return neighbors_distances, neighbors_indices
