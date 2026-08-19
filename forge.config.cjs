module.exports = {
  packagerConfig: {
    asar: true,
    name: 'Listen',
    executableName: 'Listen',
    appBundleId: 'com.scifially.listen',
    appCategoryType: 'public.app-category.education',
  },
  rebuildConfig: {},
  makers: [
    {
      name: '@electron-forge/maker-squirrel',
      config: {
        name: 'listen',
        authors: 'Scifi-ally',
        description: 'Offline bilingual lecture notes',
        setupExe: 'ListenSetup.exe',
      },
    },
    {
      name: '@electron-forge/maker-zip',
      platforms: ['win32', 'linux', 'darwin'],
    },
  ],
};
