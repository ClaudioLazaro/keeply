export const aiopsKeys = {
  all: "aiops",
  stats: () => [aiopsKeys.all, "stats"].join("::"),
  tools: () => [aiopsKeys.all, "tools"].join("::"),
  policies: () => [aiopsKeys.all, "policies"].join("::"),
  investigations: () => [aiopsKeys.all, "investigations"].join("::"),
  config: () => [aiopsKeys.all, "config"].join("::"),
  llmProviders: () => [aiopsKeys.all, "llm-providers"].join("::"),
  integrations: () => [aiopsKeys.all, "integrations"].join("::"),
};
