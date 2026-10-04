"""Genetic Algorithm optimizer for strategy parameters.

Optimizes a parameter space using selection, crossover, mutation, and
elite preservation.  Fitness is user-supplied (e.g. Sharpe, CAGR,
profit factor, or a composite).
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("nepse.optimization.genetic")

FitnessFunction = Callable[[dict[str, Any]], float]


@dataclass
class Individual:
    """One candidate solution.

    Attributes:
        genes: Parameter name → value mapping.
        fitness: Computed fitness score.
    """

    genes: dict[str, Any]
    fitness: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {"genes": self.genes, "fitness": round(self.fitness, 6)}


@dataclass
class GeneticOptimizer:
    """Genetic algorithm optimizer.

    Usage::

        optimizer = GeneticOptimizer(
            param_space={
                "rsi_period": range(10, 21),
                "ma_fast": range(5, 51, 5),
            },
            fitness_fn=lambda params: sharpe(params),
            population_size=40,
            generations=20,
        )
        best, history = optimizer.run()
    """

    param_space: dict[str, Any]
    fitness_fn: FitnessFunction
    population_size: int = 40
    generations: int = 20
    mutation_rate: float = 0.1
    crossover_rate: float = 0.7
    elite_ratio: float = 0.1
    seed: int | None = 42

    history: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate configuration."""
        if self.population_size < 4:
            raise ValueError("population_size must be at least 4.")
        if self.generations < 1:
            raise ValueError("generations must be at least 1.")
        if not 0 <= self.mutation_rate <= 1:
            raise ValueError("mutation_rate must be between 0 and 1.")
        if not 0 <= self.elite_ratio <= 1:
            raise ValueError("elite_ratio must be between 0 and 1.")
        self._rng = random.Random(self.seed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        progress_callback: Callable[[int, int, float], None] | None = None,
    ) -> tuple[Individual | None, list[dict[str, Any]]]:
        """Run the genetic optimization loop.

        Args:
            progress_callback: Optional callback invoked each generation
                with ``(generation, total, best_fitness)``.

        Returns:
            A ``(best, history)`` tuple.  ``best`` is the fittest
            individual or ``None`` when the space is empty.
        """
        if not self.param_space:
            return None, []

        population = self._init_population()
        best_overall: Individual | None = None

        for generation in range(1, self.generations + 1):
            for ind in population:
                ind.fitness = self._safe_fitness(ind.genes)

            population.sort(key=lambda i: i.fitness, reverse=True)

            if best_overall is None or population[0].fitness > best_overall.fitness:
                best_overall = Individual(
                    genes=dict(population[0].genes),
                    fitness=population[0].fitness,
                )

            self.history.append(
                {
                    "generation": generation,
                    "best_fitness": round(population[0].fitness, 6),
                    "mean_fitness": round(
                        sum(i.fitness for i in population) / len(population), 6
                    ),
                    "best_genes": dict(population[0].genes),
                }
            )

            if progress_callback is not None:
                progress_callback(
                    generation, self.generations, population[0].fitness
                )

            if generation < self.generations:
                population = self._next_generation(population)

        logger.info(
            "Genetic optimization finished: best fitness=%.4f after "
            "%d generations.",
            best_overall.fitness if best_overall else 0.0,
            self.generations,
        )
        return best_overall, list(self.history)

    # ------------------------------------------------------------------
    # GA operators
    # ------------------------------------------------------------------

    def _init_population(self) -> list[Individual]:
        """Create the initial random population."""
        population: list[Individual] = []
        for _ in range(self.population_size):
            genes = {name: self._sample(name) for name in self.param_space}
            population.append(Individual(genes=genes))
        return population

    def _sample(self, name: str) -> Any:
        """Sample a random value for a parameter."""
        values = self.param_space[name]
        if isinstance(values, range):
            return self._rng.choice(list(values))
        if isinstance(values, (list, tuple)):
            return self._rng.choice(list(values))
        if isinstance(values, dict):
            # {min: ..., max: ..., step: ...} or {low, high}
            low = float(values.get("min", values.get("low", 0)))
            high = float(values.get("max", values.get("high", 1)))
            step = float(values.get("step", 1))
            if step > 0:
                steps = int((high - low) / step) + 1
                return low + self._rng.randint(0, max(0, steps - 1)) * step
            return low + self._rng.random() * (high - low)
        # Fallback: treat as int range guess
        return float(values)

    def _mutate(self, genes: dict[str, Any]) -> dict[str, Any]:
        """Mutate genes with probability mutation_rate."""
        result = dict(genes)
        for name in result:
            if self._rng.random() < self.mutation_rate:
                result[name] = self._sample(name)
        return result

    def _crossover(
        self, parent_a: dict[str, Any], parent_b: dict[str, Any]
    ) -> dict[str, Any]:
        """Uniform crossover between two parents."""
        child: dict[str, Any] = {}
        for name in parent_a:
            if self._rng.random() < 0.5:
                child[name] = parent_a[name]
            else:
                child[name] = parent_b[name]
        return child

    def _next_generation(self, population: list[Individual]) -> list[Individual]:
        """Produce the next generation."""
        n_elite = max(1, int(len(population) * self.elite_ratio))
        next_gen: list[Individual] = [
            Individual(genes=dict(ind.genes), fitness=ind.fitness)
            for ind in population[:n_elite]
        ]

        while len(next_gen) < self.population_size:
            if self._rng.random() < self.crossover_rate and len(population) >= 2:
                a = self._tournament(population)
                b = self._tournament(population)
                child = self._crossover(a.genes, b.genes)
            else:
                parent = self._tournament(population)
                child = dict(parent.genes)

            child = self._mutate(child)
            next_gen.append(Individual(genes=child))

        return next_gen

    def _tournament(self, population: list[Individual], k: int = 3) -> Individual:
        """Select an individual via k-way tournament."""
        contenders = self._rng.sample(population, min(k, len(population)))
        return max(contenders, key=lambda i: i.fitness)

    def _safe_fitness(self, genes: dict[str, Any]) -> float:
        """Evaluate fitness, returning -inf on failure."""
        try:
            return float(self.fitness_fn(genes))
        except Exception as exc:
            logger.debug("Fitness evaluation failed: %s", exc)
            return float("-inf")
