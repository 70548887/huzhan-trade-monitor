"use strict";
const preset = document.querySelector("#interval-preset");
const minutes = document.querySelector("#interval-minutes");
preset.addEventListener("change", () => {
  if (preset.value !== "custom") minutes.value = preset.value;
  else minutes.focus();
});
minutes.addEventListener("input", () => {
  preset.value = ["5", "10", "30", "60", "120", "360"].includes(minutes.value) ? minutes.value : "custom";
});
