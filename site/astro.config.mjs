import { defineConfig } from 'astro/config';

// https://astro.build/config
export default defineConfig({
  site: 'https://felipemm.github.io',
  base: '/sssf',
  output: 'static',
  trailingSlash: 'ignore',
  build: {
    format: 'directory',
  },
});
