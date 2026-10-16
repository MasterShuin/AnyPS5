# Distributed under the OSI-approved BSD 3-Clause License.  See accompanying
# file LICENSE.rst or https://cmake.org/licensing for details.

cmake_minimum_required(VERSION ${CMAKE_VERSION}) # this file comes with cmake

# If CMAKE_DISABLE_SOURCE_CHANGES is set to true and the source directory is an
# existing directory in our source tree, calling file(MAKE_DIRECTORY) on it
# would cause a fatal error, even though it would be a no-op.
if(NOT EXISTS "C:/PROGRAMMAZIONE/REPOSITORY/AnyPS5/3rdparty/libjpeg-turbo")
  file(MAKE_DIRECTORY "C:/PROGRAMMAZIONE/REPOSITORY/AnyPS5/3rdparty/libjpeg-turbo")
endif()
file(MAKE_DIRECTORY
  "C:/PROGRAMMAZIONE/REPOSITORY/AnyPS5/build2/libjpeg-turbo-build"
  "C:/PROGRAMMAZIONE/REPOSITORY/AnyPS5/build2/libjpeg-turbo"
  "C:/PROGRAMMAZIONE/REPOSITORY/AnyPS5/build2/core/libs/prx/libSceJpegDec/jpeg_turbo_external-prefix/tmp"
  "C:/PROGRAMMAZIONE/REPOSITORY/AnyPS5/build2/core/libs/prx/libSceJpegDec/jpeg_turbo_external-prefix/src/jpeg_turbo_external-stamp"
  "C:/PROGRAMMAZIONE/REPOSITORY/AnyPS5/build2/core/libs/prx/libSceJpegDec/jpeg_turbo_external-prefix/src"
  "C:/PROGRAMMAZIONE/REPOSITORY/AnyPS5/build2/core/libs/prx/libSceJpegDec/jpeg_turbo_external-prefix/src/jpeg_turbo_external-stamp"
)

set(configSubDirs )
foreach(subDir IN LISTS configSubDirs)
    file(MAKE_DIRECTORY "C:/PROGRAMMAZIONE/REPOSITORY/AnyPS5/build2/core/libs/prx/libSceJpegDec/jpeg_turbo_external-prefix/src/jpeg_turbo_external-stamp/${subDir}")
endforeach()
if(cfgdir)
  file(MAKE_DIRECTORY "C:/PROGRAMMAZIONE/REPOSITORY/AnyPS5/build2/core/libs/prx/libSceJpegDec/jpeg_turbo_external-prefix/src/jpeg_turbo_external-stamp${cfgdir}") # cfgdir has leading slash
endif()
