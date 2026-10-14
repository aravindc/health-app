-- +goose Up
-- Glucose predictions from health-predict, recorded by health-api for every
-- new CGM reading (handlers/prediction.go), so live accuracy can be measured
-- and the model re-calibrated. One row per horizon. A reading is predicted
-- again when more pump data arrives (Tandem syncs periodically), so each
-- (reading, horizon) can have one row per pump-data watermark: the
-- prediction as first made, and with later insulin data.
create table glucose_predictions
(
    id               bigserial primary key,
    predicted_at     timestamptz not null,
    horizon_minutes  smallint    not null,
    pump_data_until  timestamptz,
    created_at       timestamptz not null default now(),
    current_mmol     real        not null,
    predicted_mmol   real        not null,
    lower_80         real,
    upper_80         real,
    lower_95         real,
    upper_95         real,
    model_version    text        not null,
    warnings         jsonb       not null default '[]',
    unique nulls not distinct (predicted_at, horizon_minutes, pump_data_until)
);

comment on table glucose_predictions is 'Predicted glucose from health-predict, one row per prediction time, horizon and pump-data watermark';
comment on column glucose_predictions.predicted_at is 'Prediction time T: the CGM reading the prediction starts from';
comment on column glucose_predictions.horizon_minutes is 'Minutes after T the prediction is for (15, 30, 45, 60)';
comment on column glucose_predictions.pump_data_until is 'How far pump data (insulin, pump CGM) reached at T when predicted; null when there was none';
comment on column glucose_predictions.created_at is 'When health-api made the prediction';
comment on column glucose_predictions.current_mmol is 'Glucose at T as the model used it (mmol/L)';
comment on column glucose_predictions.predicted_mmol is 'Predicted glucose at T + horizon (mmol/L)';
comment on column glucose_predictions.lower_80 is '80% prediction interval, lower bound (mmol/L)';
comment on column glucose_predictions.upper_80 is '80% prediction interval, upper bound (mmol/L)';
comment on column glucose_predictions.lower_95 is '95% prediction interval, lower bound (mmol/L)';
comment on column glucose_predictions.upper_95 is '95% prediction interval, upper bound (mmol/L)';
comment on column glucose_predictions.model_version is 'Model that made it, e.g. random_forest/20261008-0623-911003b';
comment on column glucose_predictions.warnings is 'Notes on the inputs from health-predict and health-api (e.g. pump data lag), as a JSON array of strings';

create index idx_glucose_predictions_predicted_at on glucose_predictions (predicted_at);

-- Each prediction with what actually happened: the Dexcom reading nearest
-- T + horizon, within 150 s (health-predict's own target tolerance), using
-- its mg/dL to mmol/L factor (18.016). actual_mmol is null until that
-- reading exists, or when there's none (a gap). Sensor LOW/HIGH readings
-- (sgv <= 39 or >= 401) are flagged: their true value is beyond the limit.
create view glucose_prediction_outcomes as
select p.*,
       a.actual_at,
       a.actual_mmol,
       a.actual_censored,
       p.predicted_mmol - a.actual_mmol                    as error_mmol,
       a.actual_mmol between p.lower_80 and p.upper_80     as within_80,
       a.actual_mmol between p.lower_95 and p.upper_95     as within_95
from glucose_predictions p
left join lateral (
    select n.ns_datetime                    as actual_at,
           n.sgv / 18.016                   as actual_mmol,
           (n.sgv <= 39 or n.sgv >= 401)    as actual_censored
    from ns_part n
    where n.ns_datetime between p.predicted_at + make_interval(mins => p.horizon_minutes) - interval '150 seconds'
                            and p.predicted_at + make_interval(mins => p.horizon_minutes) + interval '150 seconds'
    order by abs(extract(epoch from n.ns_datetime - (p.predicted_at + make_interval(mins => p.horizon_minutes))))
    limit 1
) a on true;

comment on view glucose_prediction_outcomes is 'glucose_predictions with the actual reading at T + horizon, the error (predicted - actual) and interval coverage';

-- +goose Down
DROP VIEW IF EXISTS glucose_prediction_outcomes;
DROP TABLE IF EXISTS glucose_predictions;
