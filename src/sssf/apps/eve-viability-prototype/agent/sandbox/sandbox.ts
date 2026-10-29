import { defineSandbox } from "eve/sandbox";
import { DockerSandbox } from "eve/sandbox/docker";

export const environment = DockerSandbox.dockerfile({
  pullPolicy: "never",
  env: {},
});

export default defineSandbox(() => environment.open({ networkPolicy: "deny-all" }));
