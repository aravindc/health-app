package model

type MySugr struct {
	Date                  string `json:"date"`
	Time                  string `json:"time"`
	Tags                  string `json:"tags"`
	BgreadingMmol         int8   `json:"bgreading_mmol"`
	InsulinInjUnitsPen    int8   `json:"insulin_inj_units_pen"`
	BasalInjUnits         int8   `json:"basal_inj_units"`
	BasalPumpUnits        int8   `json:"basal_pump_units"`
	BolusInjMeal          int8   `json:"bolus_inj_meal"`
	BolusInjCorrection    int8   `json:"bolus_inj_correction"`
	TempBasalPercentage   int8   `json:"temp_basal_percentage"`
	TempBasalDurationMins int8   `json:"temp_basal_duration_mins"`
	MealCarbsGrams        int8   `json:"meal_carbs_grams"`
	MealDescription       string `json:"meal_description"`
	ActivityDurationMins  int8   `json:"activity_duration_mins"`
	ActivityIntensity     int8   `json:"activity_intensity"`
	ActivityDescription   string `json:"activity_description"`
	Steps                 int8   `json:"steps"`
	Notes                 string `json:"notes"`
	Location              string `json:"location"`
	BloodPressure         string `json:"blood_pressure"`
	BodyWeightKg          int8   `json:"body_weight_kg"`
	Hba1cMmol             int8   `json:"hba1c_mmol"`
	Ketones               int    `json:"ketones"`
	FoodType              string `json:"food_type"`
	Medication            string `json:"medication"`
	Datetimestring        string `json:"datetimestring"`
	Datetimelocal         int64  `json:"datetimelocal"`
	Datetimeutc           int64  `json:"datetimeutc"`
}
