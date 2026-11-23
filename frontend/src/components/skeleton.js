// Skeleton loader helpers. Return HTML strings; shimmer is pure CSS.

export const skeletonLines = (count = 3) => Array.from({ length: count }, () =>
  `<div class="skel skel--line"></div>`
).join("");

export const skeletonStack = (count = 3) =>
  `<div class="skel-stack">${skeletonLines(count)}</div>`;

export const skeletonTiles = (count = 2) =>
  `<div class="stat-row">${Array.from({ length: count }, () =>
    `<div class="skel skel--tile"></div>`
  ).join("")}</div>`;

export const skeletonTable = (rows = 5) => `
  <div class="table-wrap">
    <table class="data-table" aria-hidden="true">
      <thead>
        <tr>
          <th class="skel skel--line-sm" style="width: 80px"></th>
          <th class="skel skel--line-sm" style="width: 100px"></th>
          <th class="skel skel--line-sm" style="width: 120px"></th>
          <th class="skel skel--line-sm" style="width: 80px"></th>
          <th class="skel skel--line-sm" style="width: 80px"></th>
        </tr>
      </thead>
      <tbody>
        ${Array.from({ length: rows }, () => `
          <tr>
            <td><div class="skel skel--line"></div></td>
            <td><div class="skel skel--line"></div></td>
            <td><div class="skel skel--line"></div></td>
            <td><div class="skel skel--line"></div></td>
            <td><div class="skel skel--line"></div></td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  </div>
`;
