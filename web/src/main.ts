import { createApp } from "vue";
import { createPinia } from "pinia";
import App from "./App.vue";
import router from "./router";
import { setUnauthorizedHandler } from "./api/client";
import "./styles/main.css";

const app = createApp(App);
app.use(createPinia());
app.use(router);

// A 401 anywhere means the session died; send the operator back to login
// instead of leaving a half-rendered page full of dashes.
setUnauthorizedHandler(() => {
  if (router.currentRoute.value.name !== "login") {
    void router.push({ name: "login", query: { next: router.currentRoute.value.fullPath } });
  }
});

app.mount("#app");
