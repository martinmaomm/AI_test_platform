<template>
  <section class="acceptance" data-testid="performance-acceptance">
    <h3>
      性能验收
      <el-tag :type="acceptanceType(result.status)">{{
        acceptanceLabel(result.status)
      }}</el-tag>
    </h3>
    <p>
      按本次运行开始前保存的标准，对全部节点汇总结果进行判定；与执行状态、AI
      建议分别展示。
    </p>
    <p v-if="result.status === 'not_configured'">
      本次未设置标准，仅展示实际指标，不自动判定达标。可在编辑压测计划时为后续运行设置。
    </p>
    <el-table v-if="result.checks?.length" :data="result.checks">
      <el-table-column label="指标"
        ><template #default="{ row }">{{
          field(row.key).label
        }}</template></el-table-column
      >
      <el-table-column label="验收标准"
        ><template #default="{ row }"
          >{{ field(row.key).operator }} {{ row.target }}
          {{ field(row.key).unit }}</template
        ></el-table-column
      >
      <el-table-column label="实际值"
        ><template #default="{ row }">{{
          row.actual == null ? "未记录" : `${row.actual} ${field(row.key).unit}`
        }}</template></el-table-column
      >
      <el-table-column label="结论"
        ><template #default="{ row }"
          ><el-tag :type="acceptanceType(row.status)">{{
            acceptanceLabel(row.status)
          }}</el-tag></template
        ></el-table-column
      >
    </el-table>
  </section>
</template>
<script setup>
import {
  acceptanceFields,
  acceptanceLabel,
  acceptanceType,
} from "./performanceAcceptanceState";
defineProps({
  result: {
    type: Object,
    default: () => ({ status: "not_configured", checks: [] }),
  },
});
const field = (key) =>
  acceptanceFields.find((item) => item.key === key) || {
    label: key,
    unit: "",
    operator: "",
  };
</script>
<style scoped>
.acceptance {
  margin: 20px 0;
}
.acceptance p {
  color: var(--app-text-secondary);
  line-height: 1.6;
}
</style>
