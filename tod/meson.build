project(
  'libfprint-goodix550c-tod',
  ['c', 'cpp'],
  version: '1.95.1+tod1-0ubuntu2.1',
  meson_version: '>= 0.64.0',
  default_options: [
    'buildtype=debugoptimized',
    'c_std=gnu11',
    'cpp_std=c++17',
    'warning_level=2',
    'b_lundef=true',
  ],
)

fs = import('fs')
cc = meson.get_compiler('c')
cpp = meson.get_compiler('cpp')

driver_source_root = get_option('driver_source_root')
sdk_root = get_option('sdk_root')
core_library = get_option('core_library')
tod_library = get_option('tod_library')

foreach option_name, option_value : {
  'driver_source_root': driver_source_root,
  'sdk_root': sdk_root,
  'core_library': core_library,
  'tod_library': tod_library,
}
  if option_value == ''
    error('-D' + option_name + ' must be provided')
  endif
endforeach

if not fs.is_dir(driver_source_root)
  error('Prepared driver source root does not exist: ' + driver_source_root)
endif
if not fs.is_dir(sdk_root)
  error('Ubuntu TOD SDK root does not exist: ' + sdk_root)
endif
if not fs.is_file(core_library)
  error('Core libfprint runtime does not exist: ' + core_library)
endif
if not fs.is_file(tod_library)
  error('TOD libfprint runtime does not exist: ' + tod_library)
endif

reproducible_path_args = [
  '-ffile-prefix-map=' + meson.project_source_root() + '=/usr/src/goodix550c-project/tod',
  '-fdebug-prefix-map=' + meson.project_source_root() + '=/usr/src/goodix550c-project/tod',
  '-ffile-prefix-map=' + meson.project_build_root() + '=/usr/src/goodix550c-build',
  '-fdebug-prefix-map=' + meson.project_build_root() + '=/usr/src/goodix550c-build',
  '-ffile-prefix-map=' + driver_source_root + '=/usr/src/goodix550c-driver',
  '-fdebug-prefix-map=' + driver_source_root + '=/usr/src/goodix550c-driver',
  '-ffile-prefix-map=' + sdk_root + '=/usr/src/ubuntu-libfprint-tod-sdk',
  '-fdebug-prefix-map=' + sdk_root + '=/usr/src/ubuntu-libfprint-tod-sdk',
]
hardening_compile_args = [
  '-D_FORTIFY_SOURCE=3',
  '-fstack-protector-strong',
]
add_project_arguments(
  reproducible_path_args + hardening_compile_args,
  language: ['c', 'cpp'],
)

sdk_public_include = sdk_root / 'usr/include/libfprint-2'
sdk_tod_include = sdk_public_include / 'tod-1'
foreach required_header : [
  sdk_public_include / 'fprint.h',
  sdk_tod_include / 'drivers_api.h',
  sdk_tod_include / 'fpi-device.h',
]
  if not fs.is_file(required_header)
    error('Exact TOD SDK header is missing: ' + required_header)
  endif
endforeach

source_manifest_path = meson.project_source_root() / 'goodix550c-sources.txt'
driver_sources = []
foreach source_line : fs.read(source_manifest_path).strip().split('\n')
  source_name = source_line.strip()
  if source_name == '' or source_name.startswith('#')
    continue
  endif
  source_path = driver_source_root / source_name
  if not fs.is_file(source_path)
    error('Allowlisted driver source is missing: ' + source_path)
  endif
  if source_name.endswith('.c') or source_name.endswith('.cpp')
    driver_sources += source_path
  endif
endforeach

glib = dependency('glib-2.0', version: '>= 2.80')
gio = dependency('gio-2.0', version: '>= 2.80')
gio_unix = dependency('gio-unix-2.0', version: '>= 2.80')
gobject = dependency('gobject-2.0', version: '>= 2.80')
gmodule = dependency('gmodule-2.0', version: '>= 2.80')
gusb = dependency('gusb', version: '>= 0.2.0')
openssl = dependency('openssl', version: '>= 3.0')

opencv_header_root = '/usr/include/opencv4'
if not cpp.has_header('opencv2/core.hpp', args: ['-I' + opencv_header_root])
  error('OpenCV component headers are unavailable below ' + opencv_header_root)
endif
opencv_headers = declare_dependency(compile_args: ['-isystem', opencv_header_root])
opencv_core = cc.find_library('opencv_core')
opencv_features2d = cc.find_library('opencv_features2d', required: false)
if not opencv_features2d.found()
  opencv_features2d = cc.find_library('opencv_features')
endif
opencv_flann = cc.find_library('opencv_flann')
opencv_imgproc = cc.find_library('opencv_imgproc')

private_headers = declare_dependency(
  compile_args: [
    '-I' + driver_source_root,
    '-I' + driver_source_root / 'drivers/goodix53x5',
    '-isystem', sdk_public_include,
    '-isystem', sdk_tod_include,
  ],
)
exact_runtime = declare_dependency(
  link_args: [tod_library, core_library],
)

driver_c_args = []
if get_option('goodix550c_volatile_init')
  driver_c_args += '-DGOODIX550C_ENABLE_VOLATILE_INIT=1'
endif
if get_option('goodix550c_manual_fdt_poll')
  driver_c_args += '-DGOODIX550C_ENABLE_MANUAL_FDT_POLL=1'
endif

symbol_map = meson.project_source_root() / 'goodix550c-tod.map'

shared_module(
  'goodix550c',
  driver_sources + files('goodix550c-tod-entry.c'),
  dependencies: [
    private_headers,
    exact_runtime,
    glib,
    gio,
    gio_unix,
    gobject,
    gmodule,
    gusb,
    openssl,
    opencv_headers,
    opencv_core,
    opencv_features2d,
    opencv_flann,
    opencv_imgproc,
  ],
  c_args: driver_c_args,
  link_args: [
    '-Wl,-z,defs',
    '-Wl,-z,relro',
    '-Wl,-z,now',
    '-Wl,-z,noexecstack',
    '-Wl,--version-script=' + symbol_map,
  ],
  link_depends: symbol_map,
  link_language: 'cpp',
  gnu_symbol_visibility: 'hidden',
  install: false,
)
