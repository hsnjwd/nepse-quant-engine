"""Genetic Optimizer page — evolutionary strategy parameter search."""

from __future__ import annotations

from typing import Any

import streamlit as st

from src.ui.helpers import section_header, divider
from src.ui.components import kpi_card


def render() -> None:
    """Render the Genetic Optimizer page."""
    section_header(
        "🧬 Genetic Optimizer",
        "Evolutionary search over strategy parameters (mutation, crossover, elites)",
    )

    try:
        from src.optimization.genetic import GeneticOptimizer
    except ImportError as exc:
        st.error(f"Genetic optimizer module unavailable: {exc}")
        return

    col1, col2 = st.columns(2)
    with col1:
        population_size = st.number_input("Population size", value=30, min_value=8, key="go_pop")
        generations = st.number_input("Generations", value=15, min_value=1, key="go_gen")
        mutation_rate = st.number_input("Mutation rate", value=0.15, min_value=0.0, max_value=1.0, step=0.05, key="go_mut")
    with col2:
        crossover_rate = st.number_input("Crossover rate", value=0.7, min_value=0.0, max_value=1.0, step=0.05, key="go_xov")
        elite_ratio = st.number_input("Elite ratio", value=0.1, min_value=0.0, max_value=0.5, step=0.05, key="go_elite")
        seed = st.number_input("Seed", value=42, key="go_seed")

    st.markdown("#### Parameter Space (JSON)")
    default_space = {
        "rsi_period": {"min": 5, "max": 30, "step": 1},
        "ma_fast": {"min": 5, "max": 50, "step": 5},
        "stop_loss_pct": {"min": 2.0, "max": 10.0, "step": 1.0},
        "take_profit_pct": {"min": 5.0, "max": 20.0, "step": 1.0},
    }
    space_text = st.text_area(
        "Param space",
        value=default_space.__str__().replace("'", '"'),
        key="go_space",
        height=180,
    )

    if st.button("▶ Run Genetic Optimization", type="primary", key="go_run"):
        try:
            import json

            param_space = json.loads(space_text)
            if not isinstance(param_space, dict) or not param_space:
                st.error("Parameter space must be a non-empty JSON object.")
                return

            # Demo fitness: maximize a composite of parameter values.
            def fitness_fn(params: dict[str, Any]) -> float:
                score = 0.0
                for key, value in params.items():
                    score += float(value) / 100.0
                return score

            optimizer = GeneticOptimizer(
                param_space=param_space,
                fitness_fn=fitness_fn,
                population_size=int(population_size),
                generations=int(generations),
                mutation_rate=float(mutation_rate),
                crossover_rate=float(crossover_rate),
                elite_ratio=float(elite_ratio),
                seed=int(seed),
            )

            progress_bar = st.progress(0.0)
            status = st.empty()

            def _cb(gen: int, total: int, best: float) -> None:
                progress_bar.progress(gen / total)
                status.caption(f"Generation {gen}/{total} — best fitness {best:.4f}")

            best, history = optimizer.run(progress_callback=_cb)
            st.session_state["go_best"] = best
            st.session_state["go_history"] = history

            # Part 7 — strategy optimization completed notification
            try:
                from src.ui.notifications import notification_manager

                notification_manager.notify_strategy(
                    "Strategy optimization completed",
                    message=f"Best fitness {best.fitness:.4f} after {len(history)} generations",
                    priority="success",
                )
            except Exception:
                pass
        except Exception as exc:
            st.error(f"Optimization failed: {exc}")

    best = st.session_state.get("go_best")
    if best:
        st.markdown("#### Best Parameters")
        cols = st.columns(4)
        for i, (key, value) in enumerate(best.genes.items()):
            with cols[i % 4]:
                kpi_card(key.replace("_", " ").title(), str(value))
        kpi_card("Fitness", f"{best.fitness:.4f}")

        history = st.session_state.get("go_history", [])
        if history:
            st.markdown("#### Evolution History")
            try:
                import plotly.graph_objects as go

                gens = [h["generation"] for h in history]
                fig = go.Figure()
                fig.add_trace(
                    go.Scatter(x=gens, y=[h["best_fitness"] for h in history], mode="lines+markers", name="Best")
                )
                fig.add_trace(
                    go.Scatter(x=gens, y=[h["mean_fitness"] for h in history], mode="lines", name="Mean")
                )
                fig.update_layout(
                    xaxis_title="Generation",
                    yaxis_title="Fitness",
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#FFFFFF"),
                    height=400,
                )
                st.plotly_chart(fig, use_container_width=True)
            except Exception as exc:
                st.info(f"Chart unavailable: {exc}")
