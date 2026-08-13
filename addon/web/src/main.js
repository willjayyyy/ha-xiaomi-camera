import { mount } from "svelte";
// The design tokens and component styles, kept as one global stylesheet so
// the page's look is bit-for-bit the pre-Svelte one -- Svelte compiles this
// into the built app.css that the bridge serves.
import "./global.css";
import App from "./App.svelte";

const app = mount(App, { target: document.getElementById("app") });

export default app;
