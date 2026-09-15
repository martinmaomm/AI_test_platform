<template>
  <ApiWorkspace :key="pageKey" source-type="browser_capture" />
</template>

<script setup>
import ApiWorkspace from "./ApiWorkspace.vue";
import { computed } from "vue";
import { useRoute } from "vue-router";
import { browserWorkspacePageKey } from "./apiWorkspace";

const route = useRoute();
// A task switch discards only the departed view's local state and invalidates
// its in-flight reads. The persisted task/workspace remains independently recoverable.
const pageKey = computed(() => browserWorkspacePageKey(route.query));
</script>
