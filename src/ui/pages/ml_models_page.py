"""ML Models page — train, evaluate, list, and predict with the ML engine."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import streamlit as st

from src.ui.helpers import section_header, divider, fmt_pct, fmt_number, safe_float
from src.ui.components import kpi_card, signal_badge


# ── Pure helpers (testable without Streamlit / sklearn) ────────────


def _trapezoid(y: Any, x: Any) -> float:
    """Integrate y over x using the trapezoidal rule (NumPy 2.x safe).

    ``np.trapezoid`` was renamed from ``np.trapz`` in NumPy 2.0; this
    helper prefers the new name and falls back for older installations.
    """
    trap = getattr(np, "trapezoid", getattr(np, "trapz", None))
    if trap is not None:
        return float(trap(y, x))
    # Manual trapezoid fallback (no NumPy required).
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    return float(np.sum((x[1:] - x[:-1]) * (y[1:] + y[:-1]) / 2.0))


def roc_curve_points(
    y_true: Any,
    y_score: Any,
    n_points: int = 25,
) -> tuple[list[float], list[float], float]:
    """Compute a ROC curve from binary labels and scores (no sklearn).

    Args:
        y_true: Binary ground-truth labels (0/1).
        y_score: Predicted probabilities for the positive class.
        n_points: Number of thresholds to evaluate.

    Returns:
        ``(fpr_list, tpr_list, auc)``.
    """
    y_true = np.asarray(y_true, dtype=float)
    y_score = np.asarray(y_score, dtype=float)
    if len(y_true) == 0 or len(np.unique(y_true)) < 2:
        return [0.0, 1.0], [0.0, 1.0], 0.5

    # Sweep thresholds from 1.0 down to 0.0 so the points trace the ROC
    # curve from (0, 0) to (1, 1); both axes are then monotonic, so the
    # trapezoid integral yields the true AUC without re-sorting.
    thresholds = np.linspace(1.0, 0.0, n_points)
    fpr: list[float] = []
    tpr: list[float] = []
    for t in thresholds:
        pred = (y_score >= t).astype(int)
        tp = float(np.sum((pred == 1) & (y_true == 1)))
        fp = float(np.sum((pred == 1) & (y_true == 0)))
        fn = float(np.sum((pred == 0) & (y_true == 1)))
        tn = float(np.sum((pred == 0) & (y_true == 0)))
        fpr.append(fp / (fp + tn) if (fp + tn) > 0 else 0.0)
        tpr.append(tp / (tp + fn) if (tp + fn) > 0 else 0.0)
    auc = float(_trapezoid(tpr, fpr))
    return fpr, tpr, auc


def pr_curve_points(
    y_true: Any,
    y_score: Any,
    n_points: int = 25,
) -> tuple[list[float], list[float], float]:
    """Compute a precision-recall curve from binary labels and scores.

    Returns:
        ``(precision_list, recall_list, average_precision)``.
    """
    y_true = np.asarray(y_true, dtype=float)
    y_score = np.asarray(y_score, dtype=float)
    if len(y_true) == 0 or np.sum(y_true) == 0:
        return [1.0, 0.0], [0.0, 1.0], 0.0

    # Sweep thresholds from 1.0 down to 0.0 so recall is monotonically
    # non-decreasing and the points trace the PR curve in order.
    thresholds = np.linspace(1.0, 0.0, n_points)
    precision: list[float] = []
    recall: list[float] = []
    for t in thresholds:
        pred = (y_score >= t).astype(int)
        tp = float(np.sum((pred == 1) & (y_true == 1)))
        fp = float(np.sum((pred == 1) & (y_true == 0)))
        fn = float(np.sum((pred == 0) & (y_true == 1)))
        precision.append(tp / (tp + fp) if (tp + fp) > 0 else 1.0)
        recall.append(tp / (tp + fn) if (tp + fn) > 0 else 0.0)
    ap = float(_trapezoid(precision, recall))
    return precision, recall, ap


def learning_curve_data(
    model_factory: Any,
    X: Any,
    y: Any,
    train_sizes: list[float] | None = None,
    seed: int = 42,
) -> dict[str, list[float]]:
    """Compute train/test accuracy across increasing training sizes.

    Args:
        model_factory: Zero-arg callable returning a fresh model wrapper.
        X: Feature matrix.
        y: Label vector (classification).
        train_sizes: Fractions of the training set to use.
        seed: Random seed for shuffling.

    Returns:
        Dict with ``train_sizes``, ``train_scores``, ``test_scores``.
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=int)
    sizes = train_sizes or [0.2, 0.4, 0.6, 0.8, 1.0]
    rng = np.random.default_rng(seed)
    n = len(y)
    order = rng.permutation(n)
    Xs, ys = X[order], y[order]
    n_test = max(1, n // 5)
    n_train_max = n - n_test

    train_sizes_out: list[float] = []
    train_scores: list[float] = []
    test_scores: list[float] = []
    for frac in sizes:
        n_train = max(2, int(n_train_max * frac))
        if n_train + n_test > n:
            n_train = max(2, n - n_test)
        X_tr, y_tr = Xs[:n_train], ys[:n_train]
        X_te, y_te = Xs[n_train : n_train + n_test], ys[n_train : n_train + n_test]
        try:
            model = model_factory()
            model.fit(X_tr, y_tr)
            train_acc = float(np.mean(model.predict(X_tr) == y_tr))
            test_acc = float(np.mean(model.predict(X_te) == y_te))
        except Exception:
            continue
        train_sizes_out.append(frac)
        train_scores.append(train_acc)
        test_scores.append(test_acc)
    return {
        "train_sizes": train_sizes_out,
        "train_scores": train_scores,
        "test_scores": test_scores,
    }


# ── Page ──────────────────────────────────────────────────────────


def render() -> None:
    """Render the ML Models page."""
    section_header(
        "🧠 ML Models",
        "Train, evaluate, list, and predict with the machine-learning subsystem",
    )

    try:
        from src.ml.models import available_models
        from src.ml.model_manager import ModelManager
    except ImportError as exc:
        st.error(f"ML module unavailable: {exc}")
        return

    model_names = available_models()
    st.caption("Available models: " + ", ".join(f"`{m}`" for m in model_names))

    tabs = st.tabs(["🎯 Predict", "🎓 Train", "📊 Evaluate", "💾 Model Manager"])

    # ── Tab 1: Predict ────────────────────────────────────────────
    with tabs[0]:
        col1, col2 = st.columns(2)
        with col1:
            symbol = st.text_input("Symbol", value="NABIL", key="ml_symbol")
            model_name = st.selectbox("Model", model_names, key="ml_model")
        with col2:
            task = st.selectbox("Task", ["classification", "regression"], key="ml_task")
            horizon = st.number_input("Horizon (days)", value=5, min_value=1, key="ml_horizon")

        if st.button("🔮 Predict", type="primary", key="ml_predict_btn"):
            try:
                from src.data import DataService
                from src.ml.prediction_engine import PredictionEngine

                history = DataService().get_history(symbol, days=365)
                if history.is_empty:
                    st.warning(f"No history available for {symbol}.")
                    return
                result = PredictionEngine().predict(
                    history.df,
                    model_name=model_name,
                    task=task,
                    horizon=int(horizon),
                )
                st.session_state["ml_prediction"] = result
            except Exception as exc:
                st.error(f"Prediction failed: {exc}")

        result = st.session_state.get("ml_prediction")
        if result:
            st.markdown("#### Prediction")
            if result.signal:
                signal_badge(result.signal)
                kpi_card("Probability", fmt_pct(result.probability))
            if result.predicted_return is not None:
                kpi_card("Predicted Return", fmt_pct(result.predicted_return))
            st.caption(f"Model: `{result.model_name}` (v{result.model_version or '—'})")
            if result.feature_importances:
                st.markdown("#### Feature Importances")
                import plotly.graph_objects as go

                features = list(result.feature_importances.keys())
                values = list(result.feature_importances.values())
                fig = go.Figure(
                    go.Bar(x=values, y=features, orientation="h", marker_color="#1F77B4")
                )
                fig.update_layout(
                    xaxis_title="Importance",
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#FFFFFF"),
                    height=400,
                )
                st.plotly_chart(fig, use_container_width=True)

    # ── Tab 2: Train ──────────────────────────────────────────────
    with tabs[1]:
        col1, col2 = st.columns(2)
        with col1:
            train_symbol = st.text_input("Train Symbol", value="NABIL", key="ml_train_symbol")
            train_model = st.selectbox("Model", model_names, key="ml_train_model")
        with col2:
            train_task = st.selectbox("Task", ["classification", "regression"], key="ml_train_task")
            train_horizon = st.number_input("Horizon", value=5, min_value=1, key="ml_train_horizon")

        if st.button("🎓 Train Model", type="primary", key="ml_train_btn"):
            try:
                from src.data import DataService
                from src.ml.training import TrainingPipeline

                history = DataService().get_history(train_symbol, days=500)
                if history.is_empty:
                    st.warning(f"No history available for {train_symbol}.")
                    return
                with st.spinner("Training model..."):
                    result = TrainingPipeline().run(
                        history.df,
                        model_name=train_model,
                        task=train_task,
                        horizon=int(train_horizon),
                    )
                st.session_state["ml_training"] = result
                st.session_state["ml_train_df"] = history.df
                # Part 7 — AI notification: training completed
                try:
                    from src.ui.notifications import notification_manager

                    notification_manager.notify_model(
                        f"Model trained: {result.model.name}",
                        message=(
                            f"Accuracy {result.metrics.get('accuracy', 0):.1%}" if result.task == "classification"
                            else f"R² {result.metrics.get('r2', 0):.3f}"
                        ),
                    )
                except Exception:
                    pass
            except Exception as exc:
                st.error(f"Training failed: {exc}")

        training = st.session_state.get("ml_training")
        if training:
            metrics = training.metrics
            cols = st.columns(4)
            if training.task == "classification":
                with cols[0]:
                    kpi_card("Accuracy", fmt_pct(metrics.get("accuracy", 0)))
                with cols[1]:
                    kpi_card("Precision", fmt_pct(metrics.get("precision", 0)))
                with cols[2]:
                    kpi_card("Recall", fmt_pct(metrics.get("recall", 0)))
                with cols[3]:
                    kpi_card("F1", fmt_pct(metrics.get("f1", 0)))
            else:
                with cols[0]:
                    kpi_card("RMSE", f"{metrics.get('rmse', 0):.4f}")
                with cols[1]:
                    kpi_card("MAE", f"{metrics.get('mae', 0):.4f}")
                with cols[2]:
                    kpi_card("R²", f"{metrics.get('r2', 0):.3f}")
                with cols[3]:
                    kpi_card("Directional", fmt_pct(metrics.get("directional_accuracy", 0)))
            st.caption(f"Trained in {training.elapsed_seconds:.2f}s using `{training.model.name}`")

    # ── Tab 3: Evaluate ───────────────────────────────────────────
    with tabs[2]:
        _render_evaluation_tab()

    # ── Tab 4: Model manager ──────────────────────────────────────
    with tabs[3]:
        manager = ModelManager()
        models = manager.list_models()
        if models:
            st.dataframe(pd.DataFrame(models), use_container_width=True, hide_index=True)
        else:
            st.info("No stored models yet. Train one in the Train tab (with persistence).")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("Save Last Training to Manager", key="ml_save_btn"):
                training = st.session_state.get("ml_training")
                if training is None:
                    st.warning("Train a model first.")
                else:
                    version = manager.save(
                        model=training.model,
                        metrics=training.metrics,
                        params={"task": training.task},
                    )
                    st.success(f"Saved model '{version.model_name}' v{version.version}")
        with col2:
            if st.button("♻️ Reset Model Store", key="ml_reset_store"):
                for m in manager.list_models():
                    manager.delete(m["name"])
                st.success("Model store cleared.")
                st.rerun()

        # ── Promote / Rollback (Part 2) ─────────────────────────
        if models:
            divider()
            st.markdown("##### 🚀 Promote / Rollback")
            sel_model = st.selectbox("Model", [m["name"] for m in models], key="ml_mgr_model")
            versions = manager.versions.list_versions(sel_model)
            if versions:
                active = manager.active_version(sel_model)
                labels = [f"v{v.version} — {v.created_at[:10]}" for v in versions]
                sel_label = st.selectbox("Version", labels, key="ml_mgr_version")
                sel_version = versions[labels.index(sel_label)].version
                col_a, col_b, col_c = st.columns(3)
                with col_a:
                    if st.button("🚀 Promote", key="ml_promote"):
                        if manager.set_active(sel_model, sel_version):
                            st.success(f"{sel_model} promoted to active v{sel_version}")
                            try:
                                from src.ui.notifications import notification_manager

                                notification_manager.notify_model(
                                    f"Model promoted: {sel_model} v{sel_version}", priority="success"
                                )
                            except Exception:
                                pass
                        else:
                            st.error("Could not promote version.")
                with col_b:
                    if st.button("⬇️ Rollback", key="ml_rollback"):
                        older = [v for v in versions if v.version != sel_version]
                        if older:
                            target = older[0].version
                            if manager.set_active(sel_model, target):
                                st.success(f"Rolled back to v{target}")
                        else:
                            st.warning("No other versions to roll back to.")
                with col_c:
                    st.caption(f"Active: {active or '—'}")


# ── Evaluation tab (Part 2) ───────────────────────────────────────


def _render_evaluation_tab() -> None:
    """Render model evaluation: confusion matrix, CV, ROC/PR, learning curve."""
    training = st.session_state.get("ml_training")
    if training is None:
        st.info("Train a model in the **Train** tab first to evaluate it.")
        return

    st.caption(
        f"Evaluating `{training.model.name}` — task: {training.task}"
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("📊 Run Full Evaluation", type="primary", key="ml_eval_btn"):
            try:
                from src.ml.cross_validation import cross_validate

                dataset = training.dataset
                if dataset is None or dataset.n_samples < 10:
                    st.warning("Dataset too small for evaluation.")
                    return

                def _factory() -> Any:
                    from src.ml.models import get_model

                    return get_model(training.model.name)

                cv = cross_validate(
                    _factory(),
                    dataset,
                    n_splits=5,
                    primary_metric="accuracy",
                )
                st.session_state["ml_cv"] = cv

                # Learning curve
                lc = learning_curve_data(
                    _factory,
                    dataset.X,
                    dataset.y,
                )
                st.session_state["ml_lc"] = lc

                # ROC / PR on a held-out split using probabilities
                try:
                    from src.ml.dataset import DatasetBuilder

                    train_ds, test_ds = DatasetBuilder.split(dataset, test_size=0.25)
                    model = _factory()
                    model.fit(train_ds.X, train_ds.y)
                    proba = model.predict_proba(test_ds.X)
                    if proba is not None and proba.shape[1] >= 2:
                        # Positive class = BUY (index 2 if present else last)
                        pos = 2 if proba.shape[1] > 2 else 1
                        y_binary = (test_ds.y == 2).astype(int)
                        fpr, tpr, auc = roc_curve_points(y_binary, proba[:, pos])
                        prec, rec, ap = pr_curve_points(y_binary, proba[:, pos])
                        st.session_state["ml_roc"] = (fpr, tpr, auc)
                        st.session_state["ml_pr"] = (prec, rec, ap)
                except Exception:
                    pass
            except Exception as exc:
                st.error(f"Evaluation failed: {exc}")

    with col2:
        if st.button("🧹 Clear Evaluation", key="ml_eval_clear"):
            for key in ("ml_cv", "ml_lc", "ml_roc", "ml_pr"):
                st.session_state.pop(key, None)
            st.rerun()

    # Confusion matrix from training metrics
    if training.task == "classification":
        cm = training.metrics.get("confusion_matrix")
        if cm:
            st.markdown("#### Confusion Matrix")
            try:
                import plotly.graph_objects as go

                fig = go.Figure(
                    go.Heatmap(
                        z=cm,
                        x=["SELL", "HOLD", "BUY"][: len(cm)],
                        y=["SELL", "HOLD", "BUY"][: len(cm)],
                        colorscale="Blues",
                        text=[[str(v) for v in row] for row in cm],
                        texttemplate="%{text}",
                    )
                )
                fig.update_layout(
                    xaxis_title="Predicted",
                    yaxis_title="Actual",
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#FFFFFF"),
                    height=350,
                )
                st.plotly_chart(fig, use_container_width=True)
            except Exception as exc:
                st.info(f"Chart unavailable: {exc}")

    # Cross-validation results
    cv = st.session_state.get("ml_cv")
    if cv:
        st.markdown("#### Cross-Validation Results")
        is_clf = training.task == "classification"
        fold_rows = []
        for r in cv.folds:
            if is_clf:
                fold_rows.append({
                    "Fold": r.fold + 1,
                    "Accuracy": round(r.metrics.get("accuracy", 0), 3),
                    "Precision": round(r.metrics.get("precision", 0), 3),
                    "Recall": round(r.metrics.get("recall", 0), 3),
                    "F1": round(r.metrics.get("f1", 0), 3),
                })
            else:
                fold_rows.append({
                    "Fold": r.fold + 1,
                    "RMSE": round(r.metrics.get("rmse", 0), 4),
                    "MAE": round(r.metrics.get("mae", 0), 4),
                    "R²": round(r.metrics.get("r2", 0), 3),
                })
        st.dataframe(pd.DataFrame(fold_rows), use_container_width=True, hide_index=True)
        if is_clf:
            st.caption(
                f"Mean accuracy: {cv.mean_metrics.get('accuracy', 0):.3f} · "
                f"Best fold: {cv.best_fold + 1}"
            )
        else:
            st.caption(
                f"Mean R²: {cv.mean_metrics.get('r2', 0):.3f} · "
                f"Mean RMSE: {cv.mean_metrics.get('rmse', 0):.4f}"
            )

    # ROC / PR curves
    roc = st.session_state.get("ml_roc")
    if roc:
        fpr, tpr, auc = roc
        try:
            import plotly.graph_objects as go

            c1, c2 = st.columns(2)
            with c1:
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=fpr, y=tpr, mode="lines", name="ROC", line=dict(color="#1F77B4")))
                fig.add_trace(go.Scatter(x=[0, 1], y=[0, 1], mode="lines", name="Random", line=dict(color="#555", dash="dash")))
                fig.update_layout(title=f"ROC (AUC {auc:.3f})", paper_bgcolor="rgba(0,0,0,0)",
                                  plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#FFFFFF"), height=320,
                                  xaxis_title="FPR", yaxis_title="TPR")
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                pr = st.session_state.get("ml_pr")
                if pr:
                    prec, rec, ap = pr
                    fig2 = go.Figure()
                    fig2.add_trace(go.Scatter(x=rec, y=prec, mode="lines", name="PR", line=dict(color="#00C853")))
                    fig2.update_layout(title=f"Precision-Recall (AP {ap:.3f})", paper_bgcolor="rgba(0,0,0,0)",
                                       plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#FFFFFF"), height=320,
                                       xaxis_title="Recall", yaxis_title="Precision")
                    st.plotly_chart(fig2, use_container_width=True)
        except Exception as exc:
            st.info(f"Curve chart unavailable: {exc}")

    # Learning curve
    lc = st.session_state.get("ml_lc")
    if lc and lc["train_sizes"]:
        st.markdown("#### Learning Curve")
        try:
            import plotly.graph_objects as go

            fig = go.Figure()
            fig.add_trace(go.Scatter(x=lc["train_sizes"], y=lc["train_scores"], mode="lines+markers",
                                     name="Train", line=dict(color="#1F77B4")))
            fig.add_trace(go.Scatter(x=lc["train_sizes"], y=lc["test_scores"], mode="lines+markers",
                                     name="Test", line=dict(color="#FF9800")))
            fig.update_layout(xaxis_title="Training size (fraction)", yaxis_title="Accuracy",
                              paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                              font=dict(color="#FFFFFF"), height=320)
            st.plotly_chart(fig, use_container_width=True)
        except Exception as exc:
            st.info(f"Learning curve unavailable: {exc}")

    # Compare models
    divider()
    st.markdown("##### ⚖️ Compare Models")
    st.caption("Cross-validate up to 3 models on the same training dataset.")
    compare_models = st.multiselect(
        "Models",
        ["random_forest", "gradient_boosting", "logistic_regression", "numpy_logistic"],
        default=["random_forest", "logistic_regression"],
        key="ml_compare_models",
        max_selections=3,
    )
    if st.button("Compare", key="ml_compare_btn") and compare_models:
        try:
            from src.ml.cross_validation import cross_validate

            dataset = training.dataset
            if dataset is None:
                st.warning("No dataset available.")
                return
            rows = []
            for name in compare_models:
                try:
                    def _factory(n: str = name) -> Any:
                        from src.ml.models import get_model

                        return get_model(n)

                    cv = cross_validate(_factory, dataset, n_splits=3, primary_metric="accuracy")
                    if training.task == "classification":
                        rows.append({
                            "Model": name,
                            "Accuracy": round(cv.mean_metrics.get("accuracy", 0), 3),
                            "Precision": round(cv.mean_metrics.get("precision", 0), 3),
                            "Recall": round(cv.mean_metrics.get("recall", 0), 3),
                            "F1": round(cv.mean_metrics.get("f1", 0), 3),
                        })
                    else:
                        rows.append({
                            "Model": name,
                            "RMSE": round(cv.mean_metrics.get("rmse", 0), 4),
                            "MAE": round(cv.mean_metrics.get("mae", 0), 4),
                            "R²": round(cv.mean_metrics.get("r2", 0), 3),
                        })
                except Exception as exc:
                    rows.append({"Model": name, "Accuracy": None, "Precision": None,
                                 "Recall": None, "F1": None, "_error": str(exc)})
            st.session_state["ml_compare"] = rows
        except Exception as exc:
            st.error(f"Comparison failed: {exc}")

    compare = st.session_state.get("ml_compare")
    if compare:
        st.dataframe(pd.DataFrame(compare), use_container_width=True, hide_index=True)
