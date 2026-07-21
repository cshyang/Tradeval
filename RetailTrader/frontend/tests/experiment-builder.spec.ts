import { expect, test } from "@playwright/test";

test("builds and starts an AI-interpreted hindsight experiment", async ({ page }) => {
  await page.route("http://localhost:4317/experiments/philosophy", (route) =>
    route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ job_id: "philosophy-job", experiment_id: "draft" }) }),
  );
  await page.route("http://localhost:4317/experiments", (route) =>
    route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ job_id: "experiment-job", experiment_id: "exp-test" }) }),
  );
  await page.route("http://localhost:4317/experiments/experiment-job", (route) =>
    route.fulfill({ contentType: "application/json", body: JSON.stringify({ jobId: "experiment-job", experimentId: "exp-test", status: "completed", stage: "completed", error: null }) }),
  );
  await page.route("http://localhost:4317/experiments/experiment-job/events", (route) =>
    route.fulfill({ contentType: "text/event-stream", body: 'id: 1\nevent: stage\ndata: {"stage":"completed"}\n\n' }),
  );

  await page.goto("/");
  await page.getByRole("button", { name: "+ New philosophy" }).click();
  await expect(page.getByRole("heading", { name: "Describe the discipline" })).toBeVisible();
  await page.getByRole("button", { name: "Interpret philosophy" }).click();
  await expect(page.getByText("AI-INTERPRETED · REVIEW BEFORE RUNNING")).toBeVisible();
  await page.getByRole("button", { name: "Configure experiment" }).click();
  await expect(page.getByText("CANDIDATE PREVIEW · APPROXIMATELY 12")).toBeVisible();
  await page.getByRole("button", { name: "Start hindsight scenario" }).click();
  await expect(page.getByText(/HINDSIGHT SCENARIO/)).toBeVisible();
  await expect(page.getByText("Deterministic intervention")).toBeVisible();
});
