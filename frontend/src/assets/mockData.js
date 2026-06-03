// mockData.js
export const mockReportData = {
  id: "SIM-2026-DBS-X92",
  outline: {
    title: "2026 DBS Support Plan: The 'Survival and Transformation' Game",
    summary: "Analysis of public sentiment regarding cash rebates and GenAI integration under high inflationary pressure.",
    sections: [
      { title: "Emotional Map Prediction", content: "### Emotional Trends\nSimulations show a **6.2/10** emotional score..." },
      { title: "Core Prediction: Defensive Gratitude", content: "The public exhibits 'defensive gratitude'—not brand loyalty, but relief." },
      { title: "Keyword Prediction: 'Cai Fan Index'", content: "Netizens are habitually converting rebates into plates of economy rice." },
      { title: "Swarm Voices: Agent Interviews", content: "Detailed feedback from simulated SME owners and professionals." }
    ]
  },
  logs: [
    { action: 'report_start', timestamp: Date.now(), details: { simulation_id: 'DBS_2026_STRESS_TEST' } },
    { action: 'planning_complete', timestamp: Date.now() + 1000, details: { message: 'Outline generated with 4 sections' } },
    { action: 'section_complete', timestamp: Date.now() + 2000, section_title: 'Emotional Map Prediction' }
  ]
};
