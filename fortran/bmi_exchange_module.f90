      module bmi_exchange_module

!!    ~ ~ ~ PURPOSE ~ ~ ~
!!    External stepping and exchange mode for coupling SWAT+ through the
!!    Basic Model Interface (BMI). The mode is off unless SWAT+ is started
!!    with the argument --exchange; with it off, none of this code changes
!!    what SWAT+ does.
!!
!!    In exchange mode SWAT+ pauses once a day, after the day's weather and
!!    decision tables and before the start-of-day water allocation, and
!!    waits for commands on standard input. Values read while paused are
!!    those of the day just completed. Values set while paused are put in
!!    place after the start-of-day water allocation and before the command
!!    (routing) loop, so SWAT+'s own daily inputs don't overwrite them.
!!    Replies go to standard output, one per line, each starting with "@".
!!    Arrays cross as raw 8-byte reals after a line that gives their length
!!    (@bin n out, @send in). Replies and arrays are written and read whole by
!!    the C helpers in bmi_fork.c, so an array costs a few system calls, not
!!    one per value, and no byte is translated on Windows.
!!    SWAT+'s own console messages go to exchange.log in the run folder.
!!    The protocol is described in docs/design.md of swatplus-bmi.
!!
!!    Fluxes and a few states can be set. A value set while paused applies
!!    to the next day only; after that day SWAT+ goes back to its own inputs.
!!    A flux is put in place at the start of the day (exch_apply) or where
!!    SWAT+ uses it (exch_channel). A state is put in place by a hook right
!!    after SWAT+ computes it and before anything uses it, replacing SWAT+'s
!!    value for that day (exch_hru_soil: soil layer temperature, after
!!    stmp_solt). A set that
!!    SWAT+ would not use as given is refused with @error, never accepted
!!    and dropped. Values go out and come in as 8-byte reals, so a value
!!    read and set back is SWAT+'s own value to the bit.
!!
!!    Chemistry travels with the water: for each salt ion SWAT+'s salt
!!    module simulates (constituents.cs), and for nitrate and soluble
!!    phosphorus, a channel's outflow concentration and its groundwater
!!    exchange mass, an aquifer's recharge mass and a point source's load.
!!    A mass set with a groundwater gain enters where SWAT+ takes in that
!!    constituent (exch_apply, exch_channel_in); a loss takes the outflow's
!!    salt and nutrients with its water (exch_channel, exch_channel_cs).
!!
!!    Some reads are counted where SWAT+ does something it keeps no record
!!    of: what a decision table's irrigate action, or a paddy's scheduled
!!    irrigation (irrp), takes from a channel or a reservoir, by source and
!!    by HRU (exch_irr_take, in actions and wet_irrp). Those hooks only add
!!    up; they change nothing SWAT+ does.
!!
!!    Irrigation can be applied to an HRU from outside SWAT+ for the next
!!    day: water into the soil and water that runs off, with the nitrate and
!!    soluble phosphorus it carries (exch_hru_irr, in hru_control after the
!!    day's management operations). It goes where SWAT+'s own irrigation
!!    goes, added to what SWAT+ applied that day.
!!
!!    The soil cover SWAT+'s soil evaporation sees can be set for an HRU's
!!    next day (exch_hru_cover, in et_act right after SWAT+ computes it):
!!    the set value replaces SWAT+'s for that day only. SWAT+ keeps no soil
!!    cover from day to day, so the set value carries on only through what
!!    the day's evaporation did.
!!
!!    Groundwater set into an HRU's soil layers goes in after the day's ET and
!!    management, before surface runoff (exch_hru_gwsoil, in hru_control), so
!!    the day's curve number sees it. Layers below a water table can be held
!!    (a state, kept until set again): a held layer doesn't drain
!!    (exch_hru_held, in swr_percmain's layer loop), the groundwater set for
!!    the day fills held layers first, and water percolating into a full held
!!    zone leaves the profile as SWAT+'s percolation, the recharge. The water
!!    swr_satexcess sends out of the top of the profile is counted
!!    (exch_hru_satex).

      use iso_fortran_env, only : input_unit
      use, intrinsic :: iso_c_binding, only : c_int, c_char, c_null_char, c_size_t, c_int32_t
      use, intrinsic :: ieee_arithmetic, only : ieee_is_finite, ieee_value, ieee_quiet_nan
      use bmi_reads_module, only : n_gen, gen_catalog, gen_value

      implicit none

      private
      public :: exch_on, exch_open, exch_start, exch_pause, exch_apply, exch_record, exch_end,     &
                exch_channel, exch_channel_in, exch_channel_cs, exch_hru_soil, exch_hru_gwsoil,   &
                exch_hru_gwsoil_mass, exch_res_in, exch_res, exch_irr_take, exch_hru_irr,   &
                exch_hru_cover, exch_hru_held, exch_hru_satex

      logical :: exch_on = .false.        !! exchange mode is on
      integer :: cmd_unit = input_unit    !! commands in
      integer :: days_done = 0            !! days completed
      integer :: days_total = 0           !! days in the simulation
      integer :: run_left = 0             !! days to run before the next pause
      logical :: run_free = .false.       !! run to the end without pausing
      integer :: protocol = 6             !! protocol version
      integer(c_int) :: bin_in = -1       !! the command channel's file descriptor, for arrays in
      integer(c_int) :: bin_out = -1      !! the reply channel's, for replies and arrays out
      logical :: ended = .false.          !! the simulation has ended (exch_end)
      integer, parameter :: unit_max = 1000000   !! highest unit number branch looks at
      character(len=80) :: paused_line = ""      !! the last @paused reply
      character(len=16), parameter :: state_magic = "SWXSTATE-10"
      !! states saved by 0.16 (SWXSTATE-9), 0.15 (SWXSTATE-8), 0.14 (SWXSTATE-7) and 0.13
      !! (SWXSTATE-6) load too: 9 is this layout without the water table (hly_held, held_lat,
      !! sat_day), 8 without the soil cover (cov_*) as well, 7 without the irrigation applied
      !! from outside (ira_*) as well, and 6 without the irrigation withdrawals (irr_*) as
      !! well, which load as zero, or as nothing set
      character(len=16), parameter :: state_magic_9 = "SWXSTATE-9"
      character(len=16), parameter :: state_magic_8 = "SWXSTATE-8"
      character(len=16), parameter :: state_magic_7 = "SWXSTATE-7"
      character(len=16), parameter :: state_magic_6 = "SWXSTATE-6"

      !! loading a saved state: run forward to the saved day, then load
      logical :: loading = .false.
      integer :: load_day = 0
      character(len=1024) :: load_path = ""

      !! files closed while they are moved or put back, to be opened again
      type unit_file
        integer :: u = 0
        character(len=1024) :: name = ""
        character(len=16) :: acc = ""
        character(len=16) :: frm = ""
        character(len=16) :: act = ""
        character(len=16) :: pos = ""
        integer :: rl = 0
      end type unit_file
      type (unit_file), dimension(:), allocatable :: closed
      integer :: n_closed = 0
      type (unit_file), dimension(:), allocatable :: saved_units   !! open at the save (save_units)

      !! values set for the next day
      real, dimension(:), allocatable :: rec_flo         !! m3/day     |point source (recall) flow
      logical, dimension(:), allocatable :: rec_flo_on
      real, dimension(:), allocatable :: dmd_flo         !! m3/day     |place of use demand
      logical, dimension(:), allocatable :: dmd_flo_on

      !! groundwater exchange with channels, m3/day, positive into the channel
      real, dimension(:), allocatable :: chx_flo         !! set for the next day
      logical, dimension(:), allocatable :: chx_on
      real, dimension(:), allocatable :: chx_gain        !! the day's gain, added to the inflow
      real, dimension(:), allocatable :: chx_loss        !! the day's loss, taken after routing
      real, dimension(:), allocatable :: chx_day         !! what was exchanged, the day just done
      real, dimension(:), allocatable :: chx_rto         !! the fraction of the day's outflow a loss took
      integer, dimension(:), allocatable :: cha_bmi      !! SWAT-deg channel number -> grid index

      !! groundwater exchange with reservoirs, m3/day, positive into the reservoir
      real, dimension(:), allocatable :: rsx_flo         !! set for the next day
      logical, dimension(:), allocatable :: rsx_on
      real, dimension(:), allocatable :: rsx_gain        !! the day's gain, added to the inflow
      real, dimension(:), allocatable :: rsx_loss        !! the day's loss, taken after SWAT+'s seepage
      !! the day just done, m3: 1 exchanged, 2 inflow, 3 outflow, 4 precipitation,
      !! 5 evaporation, 6 SWAT+'s own seepage (exch_res)
      real, dimension(:,:), allocatable :: rsv_day
      integer, dimension(:), allocatable :: res_bmi      !! reservoir number -> grid index

      !! species carried with the water: the salt ions, in constituents.cs order, then
      !! nitrate (no3, as N) and soluble phosphorus (solp, as P)
      integer :: n_salt = 0
      integer :: n_sp = 0
      character(len=16), dimension(:), allocatable :: sp_name
      !! by species and channel, kg/day: mass set with the next day's gain, the day's gain
      !! mass, and the mass exchanged the day just done (positive into the channel)
      real, dimension(:,:), allocatable :: chm_flo
      logical, dimension(:,:), allocatable :: chm_on
      real, dimension(:,:), allocatable :: chm_gain
      real, dimension(:,:), allocatable :: chm_day
      real, dimension(:,:), allocatable :: chm_wait      !! salt of a gain SWAT+ holds for a later day
      !! by species and point source, kg/day: load set for the next day
      real, dimension(:,:), allocatable :: rcm_flo
      logical, dimension(:,:), allocatable :: rcm_on

      !! HRU soil layers: grid index -> HRU number and layer; HRU number -> its first layer
      integer :: n_hly = 0
      integer, dimension(:), allocatable :: hly_hru
      integer, dimension(:), allocatable :: hly_lay
      integer, dimension(:), allocatable :: hru_hly1
      real, dimension(:), allocatable :: hly_tmp         !! deg C      |soil layer temperature set for the next day
      logical, dimension(:), allocatable :: hly_on
      real, dimension(:), allocatable :: hly_gw          !! mm/day     |groundwater into the soil layer set for the next day
      logical, dimension(:), allocatable :: hly_gw_on
      real, dimension(:), allocatable :: hly_gw_day      !! mm/day     |groundwater put into the soil layer the day just done
      !! by nutrient (1 nitrate as N, 2 labile P) and layer, kg/day: mass with that groundwater
      real, dimension(:,:), allocatable :: hly_gwm
      logical, dimension(:,:), allocatable :: hly_gwm_on
      real, dimension(:,:), allocatable :: hly_gwm_day

      !! the day's values SWAT+ zeroes when it prints them, kept at the end of the day
      real, dimension(:), allocatable :: pou_dmd_day     !! m3/day     |place of use demand
      real, dimension(:), allocatable :: pou_wdr_day     !! m3/day     |place of use withdrawal
      real, dimension(:), allocatable :: hru_irr_day     !! mm         |irrigation on the HRU (to the soil and its runoff), by HRU number

      !! what SWAT+'s irrigate actions and paddy irrigation took from channels and reservoirs, m3,
      !! added up where actions.f90 and wet_irrp.f90 take it (exch_irr_take): the day in progress (_now) and the day just
      !! done (_day, kept in exch_record). By channel and reservoir grid index, and by HRU number
      real(8), dimension(:), allocatable :: irr_cha_now, irr_cha_day
      real(8), dimension(:), allocatable :: irr_res_now, irr_res_day
      real(8), dimension(:), allocatable :: irr_hru_now, irr_hru_day

      !! irrigation applied to an HRU from outside SWAT+ (exch_hru_irr), by HRU number:
      !! 1 water into the soil and 2 water that runs off (m3/s), 3 its nitrate (as N) and
      !! 4 its soluble P (kg/s). Set for the next day (ira_set, ira_on, as given), and what
      !! went in the day just done (ira_day, m3/day and kg/day)
      integer, parameter :: n_ira = 4
      real(8), dimension(:,:), allocatable :: ira_set
      logical, dimension(:,:), allocatable :: ira_on
      real(8), dimension(:,:), allocatable :: ira_day

      !! the soil cover of SWAT+'s soil evaporation (exch_hru_cover), kg/ha, by HRU number: set
      !! for the next day, and what et_act used the day just done (NaN if it computed none)
      real, dimension(:), allocatable :: cov_set
      logical, dimension(:), allocatable :: cov_on
      real, dimension(:), allocatable :: cov_day

      !! the water table: by HRU soil layer, held below it (exch_hru_held), a state kept until
      !! set again; held_lat keeps SWAT+'s own lateral flow from a held layer (started with
      !! --held-lateral=swat); by HRU number, the saturation excess swr_satexcess sent out of
      !! the top of the profile on the day (exch_hru_satex), mm
      logical, dimension(:), allocatable :: hly_held
      logical :: held_lat = .false.
      real, dimension(:), allocatable :: sat_day
      real, dimension(:), allocatable :: nt_day          !! mm  |groundwater set and not taken, by HRU number

      !! the catalog: the water variables, the chemistry variables, one per species, then
      !! every column of the daily output records (exch_start). var_fam is 0 for a water
      !! variable, the chemistry family, or gen_fam for an output column (var_sp its number)
      integer, parameter :: gen_fam = 9
      integer, parameter :: nbase = 60
      character(len=64), dimension(nbase), parameter :: base_name = [character(len=64) ::     &
            "channel_water__volume_outflow_rate",                           &
            "channel_water__volume",                                        &
            "channel_water__volume_inflow_rate",                            &
            "aquifer_water__volume",                                        &
            "soil_water_table_surface__depth",                              &
            "aquifer_water__volume_outflow_rate",                           &
            "soil_water_table__recharge_rate",                              &
            "point_source_water__volume_flow_rate",                         &
            "place_of_use_water__volume_demand_rate",                       &
            "place_of_use_water__volume_withdrawal_rate",                  &
            "channel_water__depth",                                        &
            "channel_water__groundwater_volume_exchange_rate",             &
            "soil_layer__temperature",                                     &
            "snowpack__liquid-equivalent_depth",                           &
            "soil_water__percolation_rate",                                &
            "land_surface__evapotranspiration_rate",                       &
            "land_surface__potential_evapotranspiration_rate",             &
            "soil_water__depth",                                           &
            "atmosphere_water__precipitation_leq-volume_flux",             &
            "atmosphere_bottom_air__max_of_temperature",                   &
            "atmosphere_bottom_air__min_of_temperature",                   &
            "land_surface_radiation~incoming~shortwave__energy_flux",      &
            "atmosphere_bottom_air_water~vapor__relative_saturation",      &
            "atmosphere_bottom_air_flow__speed",                           &
            "soil_layer_water__groundwater_inflow_rate",                   &
            "soil_water__groundwater_inflow_rate",                         &
            "soil_layer_bottom__depth",                                    &
            "soil_layer__thickness",                                       &
            "soil_layer_water__depth",                                     &
            "soil_layer_water__wilting_point_depth",                       &
            "soil_layer_water__field_capacity_depth",                      &
            "soil_layer_water__saturation_depth",                          &
            "soil_layer__saturated_hydraulic_conductivity",                &
            "soil_layer_water_no3__groundwater_inflow_mass_rate",          &
            "soil_layer_water_solp__groundwater_inflow_mass_rate",         &
            "soil_water_no3__groundwater_inflow_mass_rate",                &
            "soil_water_solp__groundwater_inflow_mass_rate",              &
            "land_irrigation_water__volume_flow_rate",                    &
            "reservoir_water__volume",                                     &
            "reservoir_water_surface__area",                               &
            "reservoir_water__volume_inflow_rate",                         &
            "reservoir_water__volume_outflow_rate",                        &
            "reservoir_water__precipitation_volume_flow_rate",             &
            "reservoir_water__evaporation_volume_flow_rate",               &
            "reservoir_water__seepage_volume_flow_rate",                   &
            "reservoir_water__groundwater_volume_exchange_rate",           &
            "channel_water__irrigation_withdrawal_volume_flow_rate",       &
            "reservoir_water__irrigation_withdrawal_volume_flow_rate",     &
            "land_irrigation_water__withdrawal_volume_flow_rate",           &
            "land_irrigation_water__infiltration_volume_flow_rate",         &
            "land_irrigation_water__runoff_volume_flow_rate",               &
            "land_irrigation_water_no3__mass_flow_rate",                    &
            "land_irrigation_water_solp__mass_flow_rate",                   &
            "soil_surface_cover__mass-per-area_density",                    &
            "land_vegetation__above-ground_mass-per-area_density",          &
            "land_vegetation_leaf__mass-per-area_density",                  &
            "land_surface_residue__mass-per-area_density",                  &
            "soil_layer__saturated_zone_flag",                              &
            "land_surface_water__saturation_excess_runoff_rate",            &
            "soil_water__groundwater_inflow_not_taken_rate"]
      character(len=8), dimension(nbase), parameter :: base_grid = [character(len=8) ::      &
            "cha", "cha", "cha", "aqu", "aqu", "aqu", "aqu", "rec", "pou", "pou",    &
            "cha", "cha", "hly", "hru", "hru", "hru", "hru", "hru",                  &
            "pcp", "tmp", "tmp", "slr", "hmd", "wnd", "hly", "hru",                  &
            "hly", "hly", "hly", "hly", "hly", "hly", "hly", "hly", "hly", "hru", "hru", "hru", &
            "res", "res", "res", "res", "res", "res", "res", "res",                  &
            "cha", "res", "hru", "hru", "hru", "hru", "hru",                         &
            "hru", "hru", "hru", "hru", "hly", "hru", "hru"]
      character(len=8), dimension(nbase), parameter :: base_units = [character(len=8) ::     &
            "m3 s-1", "m3", "m3 s-1", "m3", "m", "m3 s-1", "m s-1", "m3 s-1", "m3 s-1", "m3 s-1", &
            "m", "m3 s-1", "degC", "m", "m s-1", "m s-1", "m s-1", "m",                  &
            "m s-1", "degC", "degC", "W m-2", "1", "m s-1", "m s-1", "m s-1",        &
            "m", "m", "m", "m", "m", "m", "m s-1", "kg s-1", "kg s-1", "kg s-1", "kg s-1",      &
            "m3 s-1", "m3", "m2", "m3 s-1", "m3 s-1", "m3 s-1", "m3 s-1", "m3 s-1", "m3 s-1",   &
            "m3 s-1", "m3 s-1", "m3 s-1", "m3 s-1", "m3 s-1", "kg s-1", "kg s-1",               &
            "kg m-2", "kg m-2", "kg m-2", "kg m-2", "1", "m s-1", "m s-1"]
      character(len=3), dimension(nbase), parameter :: base_role = [character(len=3) ::      &
            "out", "out", "out", "out", "out", "out", "out", "in", "in", "out",  &
            "out", "in", "in", "out", "out", "out", "out", "out",                    &
            "in", "in", "in", "in", "in", "in", "in", "out",                         &
            "out", "out", "out", "out", "out", "out", "out", "in", "in", "out", "out", "out",   &
            "out", "out", "out", "out", "out", "out", "out", "in",                   &
            "out", "out", "out", "in", "in", "in", "in",                             &
            "in", "out", "out", "out", "in", "out", "out"]
      !! chemistry families: <prefix><species><suffix>
      integer, parameter :: fam_conc = 1, fam_exch = 2, fam_rchg = 3, fam_load = 4
      character(len=24), dimension(4), parameter :: fam_prefix = [character(len=24) ::      &
            "channel_water_", "channel_water_", "aquifer_water_", "point_source_water_"]
      character(len=40), dimension(4), parameter :: fam_suffix = [character(len=40) ::      &
            "__mass_concentration", "__groundwater_mass_exchange_rate",                    &
            "__recharge_mass_rate", "__mass_flow_rate"]
      character(len=8), dimension(4), parameter :: fam_grid = [character(len=8) ::          &
            "cha", "cha", "aqu", "rec"]
      character(len=8), dimension(4), parameter :: fam_units = [character(len=8) ::         &
            "kg m-3", "kg s-1", "kg s-1", "kg s-1"]
      character(len=3), dimension(4), parameter :: fam_role = [character(len=3) ::          &
            "out", "in", "out", "in"]
      integer :: nvar = 0
      character(len=80), dimension(:), allocatable :: var_name
      character(len=8), dimension(:), allocatable :: var_grid
      character(len=16), dimension(:), allocatable :: var_units
      character(len=3), dimension(:), allocatable :: var_role
      integer, dimension(:), allocatable :: var_fam
      integer, dimension(:), allocatable :: var_sp

      !! bmi_fork.c: whole reads and writes of raw bytes on a file descriptor. gfortran
      !! reads an unformatted stream from a pipe in one read call and takes a short read
      !! for the end of the file, and writes a formatted record a value at a time with a
      !! system call each, so arrays go through these instead; and replies, so they have
      !! standard output to themselves on every platform (on Windows, a binary one)
      interface
        function swx_dup (fd) bind(c, name="swx_dup")
          import :: c_int
          integer(c_int), value :: fd
          integer(c_int) :: swx_dup
        end function swx_dup
        function swx_open (path, for_write) bind(c, name="swx_open")
          import :: c_int, c_char
          character(kind=c_char), dimension(*), intent(in) :: path
          integer(c_int), value :: for_write
          integer(c_int) :: swx_open
        end function swx_open
        subroutine swx_close (fd) bind(c, name="swx_close")
          import :: c_int
          integer(c_int), value :: fd
        end subroutine swx_close
        function swx_read (fd, buf, n) bind(c, name="swx_read")
          import :: c_int, c_size_t
          integer(c_int), value :: fd
          type(*), dimension(*) :: buf
          integer(c_size_t), value :: n
          integer(c_int) :: swx_read
        end function swx_read
        function swx_write (fd, buf, n) bind(c, name="swx_write")
          import :: c_int, c_size_t
          integer(c_int), value :: fd
          type(*), dimension(*), intent(in) :: buf
          integer(c_size_t), value :: n
          integer(c_int) :: swx_write
        end function swx_write
        function swx_write_line (fd, line, n) bind(c, name="swx_write_line")
          import :: c_int, c_char, c_size_t
          integer(c_int), value :: fd
          character(kind=c_char), dimension(*), intent(in) :: line
          integer(c_size_t), value :: n
          integer(c_int) :: swx_write_line
        end function swx_write_line
      end interface

      contains

!!    --------------------------------------------------------------------
      subroutine exch_open

!!    called first in main: turns exchange mode on when SWAT+ is started
!!    with --exchange, opens the command and reply channels on standard
!!    input and output and moves console messages to exchange.log

      character(len=64) :: arg = ""
      integer :: i = 0

      do i = 1, command_argument_count()
        call get_command_argument (i, arg)
        if (trim(arg) == "--exchange") exch_on = .true.
        if (trim(arg) == "--held-lateral=swat") held_lat = .true.
        if (trim(arg) == "--held-lateral=none") held_lat = .false.
      end do
      if (.not. exch_on) return

      !! the channels for replies and arrays as raw bytes (bmi_fork.c); commands
      !! are read as lines on unit 5
      bin_in = swx_dup (0_c_int)
      bin_out = swx_dup (1_c_int)
      if (bin_in < 0 .or. bin_out < 0) then
        write (0, '(a)') "exchange mode: can't duplicate standard input and output"
        error stop 5
      end if

      !! replies have standard output to themselves; unit 6 (the console) goes to a file
      open (6, file="exchange.log", action="write", status="replace")

      return
      end subroutine exch_open

!!    --------------------------------------------------------------------
      subroutine exch_start

!!    called in time_control before the year loop: sends the catalog

      use time_module
      use hydrograph_module, only : sp_ob, sp_ob1, ob
      use calibration_data_module, only : cal_soft, cal_hard
      use basin_module, only : prog
      use maximum_data_module, only : db_mx
      use bmi_build_module, only : swx_build
      use soil_module, only : soil
      use constituent_mass_module, only : cs_db

      integer :: iyr = 0
      integer :: yr = 0
      integer :: ndy = 0
      integer :: d0 = 0
      integer :: d1 = 0
      integer :: iv = 0
      integer :: ihru = 0
      integer :: ly = 0
      integer :: k = 0
      integer :: f = 0

      if (.not. exch_on) return

      if (cal_soft == "y" .or. cal_hard == "y") then
        call reply ("@error exchange mode does not run with soft or hard calibration")
        error stop 2
      end if

      !! days in the simulation, counted the way time_control counts them
      days_total = 0
      yr = time%yrc_start
      do iyr = 1, time%nbyr
        ndy = 365
        if (Mod(yr,4) == 0 .and. (Mod(yr,100) /= 0 .or. Mod(yr,400) == 0)) ndy = 366
        d0 = 1
        if (iyr == 1 .and. time%day_start > 0) d0 = time%day_start
        d1 = ndy
        if (iyr == time%nbyr .and. time%day_end > 0) d1 = min (time%day_end, ndy)
        days_total = days_total + d1 - d0 + 1
        yr = yr + 1
      end do

      allocate (rec_flo(sp_ob%recall), source = 0.)
      allocate (rec_flo_on(sp_ob%recall), source = .false.)
      allocate (dmd_flo(db_mx%wallo_pou), source = 0.)
      allocate (dmd_flo_on(db_mx%wallo_pou), source = .false.)
      allocate (pou_dmd_day(db_mx%wallo_pou), source = 0.)
      allocate (pou_wdr_day(db_mx%wallo_pou), source = 0.)
      allocate (hru_irr_day(sp_ob%hru), source = 0.)
      allocate (irr_cha_now(sp_ob%chandeg), source = 0.d0)
      allocate (irr_cha_day(sp_ob%chandeg), source = 0.d0)
      allocate (irr_res_now(sp_ob%res), source = 0.d0)
      allocate (irr_res_day(sp_ob%res), source = 0.d0)
      allocate (irr_hru_now(sp_ob%hru), source = 0.d0)
      allocate (irr_hru_day(sp_ob%hru), source = 0.d0)
      allocate (ira_set(n_ira,sp_ob%hru), source = 0.d0)
      allocate (ira_on(n_ira,sp_ob%hru), source = .false.)
      allocate (ira_day(n_ira,sp_ob%hru), source = 0.d0)
      allocate (cov_set(sp_ob%hru), source = 0.)
      allocate (cov_on(sp_ob%hru), source = .false.)
      allocate (cov_day(sp_ob%hru), source = ieee_value (0., ieee_quiet_nan))
      allocate (chx_flo(sp_ob%chandeg), source = 0.)
      allocate (chx_on(sp_ob%chandeg), source = .false.)
      allocate (chx_gain(sp_ob%chandeg), source = 0.)
      allocate (chx_loss(sp_ob%chandeg), source = 0.)
      allocate (chx_day(sp_ob%chandeg), source = 0.)
      allocate (chx_rto(sp_ob%chandeg), source = 0.)
      allocate (cha_bmi(0:max(1, maxval([0, (ob(sp_ob1%chandeg + iv - 1)%num, iv = 1, sp_ob%chandeg)]))), source = 0)
      allocate (rsx_flo(sp_ob%res), source = 0.)
      allocate (rsx_on(sp_ob%res), source = .false.)
      allocate (rsx_gain(sp_ob%res), source = 0.)
      allocate (rsx_loss(sp_ob%res), source = 0.)
      allocate (rsv_day(6,sp_ob%res), source = 0.)
      allocate (res_bmi(0:max(1, maxval([0, (ob(sp_ob1%res + iv - 1)%num, iv = 1, sp_ob%res)]))), source = 0)
      do iv = 1, sp_ob%res
        res_bmi(ob(sp_ob1%res + iv - 1)%num) = iv
      end do
      do iv = 1, sp_ob%chandeg
        cha_bmi(ob(sp_ob1%chandeg + iv - 1)%num) = iv
      end do

      !! HRU soil layers, HRU by HRU in grid order
      n_hly = 0
      do iv = 1, sp_ob%hru
        n_hly = n_hly + soil(ob(sp_ob1%hru + iv - 1)%num)%nly
      end do
      allocate (hly_hru(n_hly), source = 0)
      allocate (hly_lay(n_hly), source = 0)
      allocate (hru_hly1(0:max(1, maxval([0, (ob(sp_ob1%hru + iv - 1)%num, iv = 1, sp_ob%hru)]))), source = 0)
      allocate (hly_tmp(n_hly), source = 0.)
      allocate (hly_on(n_hly), source = .false.)
      allocate (hly_gw(n_hly), source = 0.)
      allocate (hly_gw_on(n_hly), source = .false.)
      allocate (hly_gw_day(n_hly), source = 0.)
      allocate (hly_gwm(2,n_hly), source = 0.)
      allocate (hly_gwm_on(2,n_hly), source = .false.)
      allocate (hly_gwm_day(2,n_hly), source = 0.)
      allocate (hly_held(n_hly), source = .false.)
      allocate (sat_day(sp_ob%hru), source = 0.)
      allocate (nt_day(sp_ob%hru), source = 0.)
      n_hly = 0
      do iv = 1, sp_ob%hru
        ihru = ob(sp_ob1%hru + iv - 1)%num
        hru_hly1(ihru) = n_hly + 1
        do ly = 1, soil(ihru)%nly
          n_hly = n_hly + 1
          hly_hru(n_hly) = ihru
          hly_lay(n_hly) = ly
        end do
      end do

      !! species carried with the water, and the catalog
      n_salt = cs_db%num_salts
      n_sp = n_salt + 2
      allocate (sp_name(n_sp))
      do k = 1, n_salt
        sp_name(k) = adjustl(cs_db%salts(k))
      end do
      sp_name(n_salt + 1) = "no3"
      sp_name(n_salt + 2) = "solp"
      allocate (chm_flo(n_sp, sp_ob%chandeg), source = 0.)
      allocate (chm_on(n_sp, sp_ob%chandeg), source = .false.)
      allocate (chm_gain(n_sp, sp_ob%chandeg), source = 0.)
      allocate (chm_day(n_sp, sp_ob%chandeg), source = 0.)
      allocate (chm_wait(max(n_salt, 1), sp_ob%chandeg), source = 0.)
      allocate (rcm_flo(n_sp, sp_ob%recall), source = 0.)
      allocate (rcm_on(n_sp, sp_ob%recall), source = .false.)

      nvar = nbase + 4 * n_sp + n_gen
      allocate (var_name(nvar), var_grid(nvar), var_units(nvar), var_role(nvar))
      allocate (var_fam(nvar), source = 0)
      allocate (var_sp(nvar), source = 0)
      var_name(1:nbase) = base_name
      var_grid(1:nbase) = base_grid
      var_units(1:nbase) = base_units
      var_role(1:nbase) = base_role
      iv = nbase
      do f = 1, 4
        do k = 1, n_sp
          iv = iv + 1
          var_name(iv) = trim(fam_prefix(f)) // trim(sp_name(k)) // trim(fam_suffix(f))
          var_grid(iv) = fam_grid(f)
          var_units(iv) = fam_units(f)
          var_role(iv) = fam_role(f)
          var_fam(iv) = f
          var_sp(iv) = k
        end do
      end do
      !! every column of the daily output records, read-only (bmi_reads_module)
      call gen_catalog (var_name(iv+1:), var_grid(iv+1:), var_units(iv+1:))
      do k = 1, n_gen
        var_role(iv + k) = "out"
        var_fam(iv + k) = gen_fam
        var_sp(iv + k) = k
      end do

      call reply ("@hello swatplus-exchange " // itoa(protocol))
      call reply ("@model " // trim(adjustl(prog)))
      call reply ("@build " // swx_build)
      call reply ("@start " // itoa(time%yrc_start) // " " // itoa(max(1, time%day_start)))
      call reply ("@days " // itoa(days_total))
      call send_grid ("cha", sp_ob1%chandeg, sp_ob%chandeg)
      call send_grid ("aqu", sp_ob1%aqu, sp_ob%aqu)
      call send_grid ("rec", sp_ob1%recall, sp_ob%recall)
      call send_grid ("res", sp_ob1%res, sp_ob%res)
      call send_pou_grid
      call send_grid ("hru", sp_ob1%hru, sp_ob%hru)
      call send_hly_grid
      call send_wx_grid ("pcp", 1)
      call send_wx_grid ("tmp", 2)
      call send_wx_grid ("slr", 4)
      call send_wx_grid ("hmd", 5)
      call send_wx_grid ("wnd", 6)
      call send_lsu_grid
      call send_bsn_grid
      do iv = 1, nvar
        call reply ("@var " // trim(var_name(iv)) // " " // trim(var_grid(iv)) // " " //   &
                    trim(var_role(iv)) // " " // trim(var_units(iv)))
      end do
      call reply ("@ready")

      return
      end subroutine exch_start

!!    --------------------------------------------------------------------
      logical function exch_pause ()

!!    called in time_control each day before wallo_start_day, as
!!    "if (exch_pause()) cycle": reports the days done and waits for
!!    commands. Returns true to skip the rest of the day, which only happens
!!    while running forward to the day of a saved state being loaded.

      use time_module

      exch_pause = .false.
      if (.not. exch_on) return

      do
        if (loading) then
          if (days_done < load_day) then
            days_done = days_done + 1
            exch_pause = .true.
            return
          end if
          call load_now
        else if (.not. run_free .and. run_left == 0) then
          paused_line = "@paused " // itoa(days_done) // " " // itoa(time%yrc) // " " // itoa(time%day)
        end if
        if (run_free .or. run_left > 0) exit
        call reply (paused_line)
        call serve (.false.)
        if (.not. loading) exit
      end do

      if (.not. run_free) run_left = run_left - 1
      days_done = days_done + 1

      return
      end function exch_pause

!!    --------------------------------------------------------------------
      subroutine exch_apply

!!    called in time_control each day after wallo_start_day and before
!!    call command: puts values set while paused in place for the day,
!!    then forgets them

      use time_module
      use hydrograph_module, only : sp_ob1, ob, recall
      use constituent_mass_module, only : rec_salt

      integer :: i = 0
      integer :: irec = 0
      integer :: iob = 0
      integer :: k = 0

      if (.not. exch_on) return

      do i = 1, size(rec_flo)
        irec = ob(sp_ob1%recall + i - 1)%num
        if (rec_flo_on(i)) then
          recall(irec)%hd(time%day,time%yrs)%flo = rec_flo(i)
          rec_flo_on(i) = .false.
        end if
        !! loads go in the day's records SWAT+ reads them from (command, recall_salt)
        do k = 1, n_sp
          if (.not. rcm_on(k,i)) cycle
          if (k <= n_salt) then
            rec_salt(irec)%hd_salt(time%day,time%yrs)%salt(k) = rcm_flo(k,i)
          else if (k == n_salt + 1) then
            recall(irec)%hd(time%day,time%yrs)%no3 = rcm_flo(k,i)
          else
            recall(irec)%hd(time%day,time%yrs)%solp = rcm_flo(k,i)
          end if
          rcm_on(k,i) = .false.
        end do
      end do

      do i = 1, size(chx_flo)
        chx_gain(i) = 0.
        chx_loss(i) = 0.
        chm_gain(:,i) = 0.
        if (chx_on(i)) then
          if (chx_flo(i) > 0.) then
            !! a gain joins the channel's inflow through SWAT+'s transfer hydrograph, with
            !! its nitrate and phosphorus; its salt joins the incoming load (exch_channel_in)
            iob = sp_ob1%chandeg + i - 1
            call trans_cs_ready (iob)
            ob(iob)%trans%flo = ob(iob)%trans%flo + chx_flo(i)
            chx_gain(i) = chx_flo(i)
            do k = 1, n_sp
              if (chm_on(k,i)) chm_gain(k,i) = chm_flo(k,i)
            end do
            ob(iob)%trans%no3 = ob(iob)%trans%no3 + chm_gain(n_salt + 1,i)
            ob(iob)%trans%solp = ob(iob)%trans%solp + chm_gain(n_salt + 2,i)
          else if (chx_flo(i) < 0.) then
            chx_loss(i) = - chx_flo(i)
          end if
        end if
        chx_on(i) = .false.
        chm_on(:,i) = .false.
      end do

      !! a reservoir's gain joins its inflow (exch_res_in), its loss goes after SWAT+'s
      !! own seepage (exch_res); a reservoir not yet built that day takes neither
      rsv_day = 0.
      do i = 1, size(rsx_flo)
        rsx_gain(i) = 0.
        rsx_loss(i) = 0.
        if (rsx_on(i)) then
          if (rsx_flo(i) > 0.) then
            rsx_gain(i) = rsx_flo(i)
          else
            rsx_loss(i) = - rsx_flo(i)
          end if
        end if
        rsx_on(i) = .false.
      end do

      do i = 1, size(dmd_flo)
        if (.not. dmd_flo_on(i)) cycle
        call set_demand (i, dmd_flo(i))
        dmd_flo_on(i) = .false.
      end do

      !! groundwater into the soil goes in at exch_hru_gwsoil, HRU by HRU, and its
      !! nitrate and phosphorus at exch_hru_gwsoil_mass
      hly_gw_day = 0.
      hly_gwm_day = 0.
      !! irrigation from outside goes in at exch_hru_irr, HRU by HRU
      ira_day = 0.d0
      !! the soil cover at exch_hru_cover: NaN for an HRU whose et_act computes none
      cov_day = ieee_value (0., ieee_quiet_nan)
      !! the saturation excess, added up at exch_hru_satex
      sat_day = 0.
      !! groundwater not taken, at exch_hru_gwsoil
      nt_day = 0.

      return
      end subroutine exch_apply

!!    --------------------------------------------------------------------
      subroutine trans_cs_ready (iob)

!!    SWAT+ 62.0.1 never allocates an object's transfer constituents
!!    (obcs%trans), but with constituents on, sd_channel_control3 adds them
!!    to the incoming load whenever the transfer has water. That reads past
!!    the arrays (an error with bounds checked, a hang without), for a
!!    water allocation return as for a gain. Before a gain goes into the
!!    transfer, this allocates them, zero, if SWAT+ hasn't; SWAT+ then sets
!!    the incoming load again before using it, so no value changes.

      use constituent_mass_module, only : obcs, obcs_alloc, cs_db

      integer, intent(in) :: iob

      if (cs_db%num_tot == 0) return
      if (obcs_alloc(iob) /= 1) return
      associate (t => obcs(iob)%trans)
        if (.not. allocated(t%pest)) allocate (t%pest(cs_db%num_pests), source = 0.)
        if (.not. allocated(t%path)) allocate (t%path(cs_db%num_paths), source = 0.)
        if (.not. allocated(t%hmet)) allocate (t%hmet(cs_db%num_metals), source = 0.)
        if (.not. allocated(t%salt)) allocate (t%salt(cs_db%num_salts), source = 0.)
        if (.not. allocated(t%cs)) allocate (t%cs(cs_db%num_cs), source = 0.)
      end associate

      return
      end subroutine trans_cs_ready

!!    --------------------------------------------------------------------
      subroutine exch_channel (ich)

!!    called in sd_channel_control3 after routing and water allocation, before
!!    the outflow is set: takes a set groundwater loss from the day's outflow,
!!    no more than is there, with the outflow's sediment and nutrients, and
!!    counts it as seepage so SWAT+'s printed channel balance still closes;
!!    records what was exchanged. The outflow's salt goes later, once SWAT+
!!    has split it between outflow and storage (exch_channel_cs).

      use hydrograph_module, only : ht2, operator(*)
      use water_body_module, only : ch_wat_d

      integer, intent(in) :: ich                          !SWAT-deg channel number
      integer :: i = 0
      real :: rto = 0.
      real :: taken = 0.

      if (.not. exch_on) return
      if (ich < lbound(cha_bmi,1) .or. ich > ubound(cha_bmi,1)) return
      i = cha_bmi(ich)
      if (i == 0) return

      taken = 0.
      rto = 0.
      if (chx_loss(i) > 0. .and. ht2%flo > 0.) then
        rto = Min(1., chx_loss(i) / ht2%flo)
        taken = rto * ht2%flo
      end if
      chm_day(n_salt + 1,i) = chm_gain(n_salt + 1,i) - rto * ht2%no3
      chm_day(n_salt + 2,i) = chm_gain(n_salt + 2,i) - rto * ht2%solp
      if (rto > 0.) then
        ht2 = (1. - rto) * ht2
        ch_wat_d(ich)%seep = ch_wat_d(ich)%seep + taken
      end if
      chx_rto(i) = rto
      chx_day(i) = chx_gain(i) - taken
      chx_gain(i) = 0.
      chx_loss(i) = 0.

      return
      end subroutine exch_channel

!!    --------------------------------------------------------------------
      subroutine exch_res_in (jres)

!!    called in res_control right after the day's inflow is added to the
!!    reservoir: a set groundwater gain joins the inflow (res and ht1, so the
!!    printed inflow shows it and SWAT+'s release rules see it, as a channel's
!!    gain joins its inflow). Groundwater carries no sediment or nutrients.

      use hydrograph_module, only : ht1, res

      integer, intent(in) :: jres                         !reservoir number
      integer :: i = 0

      if (.not. exch_on) return
      if (jres < lbound(res_bmi,1) .or. jres > ubound(res_bmi,1)) return
      i = res_bmi(jres)
      if (i == 0) return

      if (rsx_gain(i) > 0.) then
        res(jres)%flo = res(jres)%flo + rsx_gain(i)
        ht1%flo = ht1%flo + rsx_gain(i)
      end if

      return
      end subroutine exch_res_in

!!    --------------------------------------------------------------------
      subroutine exch_res (jres)

!!    called in res_control after SWAT+ takes the day's outflow, evaporation
!!    and seepage from the reservoir, before its area is updated: takes a set
!!    groundwater loss, no more than the reservoir holds, and counts it as
!!    seepage so SWAT+'s printed reservoir balance still closes. As SWAT+'s own
!!    seepage, the loss takes water only. Keeps the day's water for reading.

      use water_body_module, only : res_wat_d
      use hydrograph_module, only : ht1, ht2, res

      integer, intent(in) :: jres                         !reservoir number
      integer :: i = 0
      real :: taken = 0.

      if (.not. exch_on) return
      if (jres < lbound(res_bmi,1) .or. jres > ubound(res_bmi,1)) return
      i = res_bmi(jres)
      if (i == 0) return

      rsv_day(6,i) = res_wat_d(jres)%seep
      taken = 0.
      if (rsx_loss(i) > 0.) then
        taken = Min(rsx_loss(i), Max(0., res(jres)%flo))
        res(jres)%flo = res(jres)%flo - taken
        res_wat_d(jres)%seep = res_wat_d(jres)%seep + taken
      end if
      rsv_day(1,i) = rsx_gain(i) - taken
      rsv_day(2,i) = ht1%flo
      rsv_day(3,i) = ht2%flo
      rsv_day(4,i) = res_wat_d(jres)%precip
      rsv_day(5,i) = res_wat_d(jres)%evap
      rsx_gain(i) = 0.
      rsx_loss(i) = 0.

      return
      end subroutine exch_res

!!    --------------------------------------------------------------------
      subroutine exch_irr_take (j, src, isrc)

!!    called in actions, case irrigate, right after the action has taken its
!!    water from its source, and in wet_irrp (a paddy's scheduled irrigation,
!!    irrp) right after it has taken from a channel or a reservoir: adds up,
!!    by source and by HRU, what was taken from a channel (cha, sdc) or a
!!    reservoir (res). That's irrig(j)%water%flo, rto times the source's
!!    storage before the take (m3); an irrigate action's is 0 when the source
!!    held too little and SWAT+ gave none. It only
!!    reads: SWAT+ doesn't tag irrig(j)%demand with its source, so the
!!    withdrawal is counted here, where it happens. Aquifers and outside
!!    sources aren't counted.

      use hydrograph_module, only : irrig

      integer, intent(in) :: j                            !HRU number
      character(len=*), intent(in) :: src                 !the action's source object type
      integer, intent(in) :: isrc                         !the source's number
      integer :: i = 0
      real(8) :: v = 0.d0

      if (.not. exch_on) return
      if (.not. allocated(irr_hru_now)) return
      if (j < 1 .or. j > size(irr_hru_now)) return

      select case (src)
      case ("cha", "sdc")
        if (isrc < lbound(cha_bmi,1) .or. isrc > ubound(cha_bmi,1)) return
        i = cha_bmi(isrc)
        if (i == 0) return
        v = irrig(j)%water%flo
        irr_cha_now(i) = irr_cha_now(i) + v
      case ("res")
        if (isrc < lbound(res_bmi,1) .or. isrc > ubound(res_bmi,1)) return
        i = res_bmi(isrc)
        if (i == 0) return
        v = irrig(j)%water%flo
        irr_res_now(i) = irr_res_now(i) + v
      case default
        return
      end select
      irr_hru_now(j) = irr_hru_now(j) + v

      return
      end subroutine exch_irr_take

!!    --------------------------------------------------------------------
      subroutine exch_channel_in (ich)

!!    called in sd_channel_control3 right after the channel's incoming salt
!!    load is set (hcs1): adds the salt set with the day's groundwater gain,
!!    as SWAT+ adds an aquifer's. SWAT+ 62.0.1 sets hcs1 again after adding
!!    a transfer's salt (obcs%trans), so the salt can't go with the water
!!    the way the nitrate and phosphorus do. A transfer under 1e-6 m3 waits
!!    in SWAT+'s transfer hydrograph for a later day, and its salt waits with it.

      use hydrograph_module, only : sp_ob1, ob
      use constituent_mass_module, only : hcs1

      integer, intent(in) :: ich                          !SWAT-deg channel number
      integer :: i = 0
      integer :: k = 0

      if (.not. exch_on .or. n_salt == 0) return
      if (ich < lbound(cha_bmi,1) .or. ich > ubound(cha_bmi,1)) return
      i = cha_bmi(ich)
      if (i == 0) return

      chm_wait(1:n_salt,i) = chm_wait(1:n_salt,i) + chm_gain(1:n_salt,i)
      if (ob(sp_ob1%chandeg + i - 1)%trans%flo > 0.) return
      do k = 1, n_salt
        hcs1%salt(k) = hcs1%salt(k) + chm_wait(k,i)
      end do
      chm_wait(1:n_salt,i) = 0.

      return
      end subroutine exch_channel_in

!!    --------------------------------------------------------------------
      subroutine exch_channel_cs (ich)

!!    called in sd_channel_control3 after SWAT+ splits the channel's salt and
!!    other constituents between outflow (hcs2) and storage: a set loss takes
!!    the same fraction of the outflow's salt and constituents as of its
!!    water (exch_channel), counted as seepage as SWAT+ counts its own
!!    seepage's; records the salt exchanged

      use constituent_mass_module, only : hcs2, cs_db
      use ch_salt_module, only : chsalt_d
      use ch_cs_module, only : chcs_d

      integer, intent(in) :: ich                          !SWAT-deg channel number
      integer :: i = 0
      integer :: k = 0
      real :: taken = 0.

      if (.not. exch_on) return
      if (ich < lbound(cha_bmi,1) .or. ich > ubound(cha_bmi,1)) return
      i = cha_bmi(ich)
      if (i == 0) return

      do k = 1, n_salt
        taken = chx_rto(i) * hcs2%salt(k)
        if (taken > 0.) then
          hcs2%salt(k) = hcs2%salt(k) - taken
          chsalt_d(ich)%salt(k)%seep = chsalt_d(ich)%salt(k)%seep + taken
        end if
        chm_day(k,i) = chm_gain(k,i) - taken
      end do
      do k = 1, cs_db%num_cs
        taken = chx_rto(i) * hcs2%cs(k)
        if (taken > 0.) then
          hcs2%cs(k) = hcs2%cs(k) - taken
          chcs_d(ich)%cs(k)%seep = chcs_d(ich)%cs(k)%seep + taken
        end if
      end do
      chx_rto(i) = 0.
      chm_gain(:,i) = 0.

      return
      end subroutine exch_channel_cs

!!    --------------------------------------------------------------------
      subroutine exch_hru_soil (j)

!!    called in hru_control right after stmp_solt, before snowmelt, runoff
!!    and percolation use the soil temperature: replaces the day's computed
!!    temperature of the HRU's layers that were set for the day. SWAT+ then
!!    carries the set value into the next day as it would its own.

      use soil_module, only : soil

      integer, intent(in) :: j                            !HRU number
      integer :: k = 0
      integer :: i = 0

      if (.not. exch_on) return
      if (j < lbound(hru_hly1,1) .or. j > ubound(hru_hly1,1)) return
      if (hru_hly1(j) == 0) return
      do k = 1, soil(j)%nly
        i = hru_hly1(j) + k - 1
        if (.not. hly_on(i)) cycle
        soil(j)%phys(k)%tmp = hly_tmp(i)
        hly_on(i) = .false.
      end do

      return
      end subroutine exch_hru_soil

!!    --------------------------------------------------------------------
      subroutine exch_hru_gwsoil (j)

!!    called in hru_control right after mgt_operatn and exch_hru_irr, before
!!    surface: adds the groundwater set for the day to the HRU's soil layers
!!    and counts it as SWAT+ counts gwflow's (gwsoilq, printed in hru_wb as
!!    gwsoilq), then sums the profile's water again (soil(j)%sw), which the
!!    day's curve number (sq_dailycn) reads. In an HRU with held layers (below
!!    a water table, exch_hru_held) the HRU's whole set inflow fills the held
!!    layers first, bottom up, to saturation (ul); the rest goes to the unheld
!!    layers in proportion to what was set on them, or, with none set on
!!    them, to the lowest; with every layer held, the rest isn't taken. What
!!    went into each layer is kept for reading (hly_gw_day), and what wasn't
!!    taken (nt_day)

      use soil_module, only : soil
      use hru_module, only : gwsoilq

      integer, intent(in) :: j                            !HRU number
      integer :: k = 0
      integer :: i = 0
      integer :: i1 = 0                                   !the HRU's first layer on the hly grid
      integer :: nly = 0
      integer :: kt = 0                                   !the top held layer
      real :: rest = 0.                                   !mm  |set inflow still to place
      real :: setun = 0.                                  !mm  |set on the unheld layers
      real :: add = 0.                                    !mm

      if (.not. exch_on) return
      if (j < lbound(hru_hly1,1) .or. j > ubound(hru_hly1,1)) return
      if (hru_hly1(j) == 0) return
      i1 = hru_hly1(j)
      nly = soil(j)%nly
      if (.not. any(hly_gw_on(i1:i1+nly-1))) return
      if (.not. any(hly_held(i1:i1+nly-1))) then
        do k = 1, nly
          i = i1 + k - 1
          if (.not. hly_gw_on(i)) cycle
          soil(j)%phys(k)%st = soil(j)%phys(k)%st + hly_gw(i)
          gwsoilq(j) = gwsoilq(j) + hly_gw(i)
          hly_gw_day(i) = hly_gw(i)
        end do
      else
        rest = 0.
        setun = 0.
        do k = 1, nly
          i = i1 + k - 1
          if (.not. hly_gw_on(i)) cycle
          rest = rest + hly_gw(i)
          if (.not. hly_held(i)) setun = setun + hly_gw(i)
        end do
        !! held layers are the bottom ones (set_held): fill them from the bottom up
        kt = nly + 1
        do k = nly, 1, -1
          i = i1 + k - 1
          if (.not. hly_held(i)) exit
          kt = k
          add = min(rest, max(0., soil(j)%phys(k)%ul - soil(j)%phys(k)%st))
          soil(j)%phys(k)%st = soil(j)%phys(k)%st + add
          gwsoilq(j) = gwsoilq(j) + add
          hly_gw_day(i) = add
          rest = rest - add
        end do
        !! the rest to the unheld layers above
        if (rest > 0. .and. kt > 1) then
          if (setun > 0.) then
            do k = 1, kt - 1
              i = i1 + k - 1
              if (.not. hly_gw_on(i)) cycle
              add = rest * (hly_gw(i) / setun)
              soil(j)%phys(k)%st = soil(j)%phys(k)%st + add
              gwsoilq(j) = gwsoilq(j) + add
              hly_gw_day(i) = add
            end do
          else
            i = i1 + kt - 2
            soil(j)%phys(kt-1)%st = soil(j)%phys(kt-1)%st + rest
            gwsoilq(j) = gwsoilq(j) + rest
            hly_gw_day(i) = rest
          end if
        else if (rest > 0.) then
          if (j <= size(nt_day)) nt_day(j) = rest           !every layer held and full
        end if
      end if
      hly_gw_on(i1:i1+nly-1) = .false.
      soil(j)%sw = 0.
      do k = 1, nly
        soil(j)%sw = soil(j)%sw + soil(j)%phys(k)%st
      end do

      return
      end subroutine exch_hru_gwsoil

!!    --------------------------------------------------------------------
      subroutine exch_hru_held (j, j1)

!!    called in swr_percmain's layer loop right after swr_percmicro has taken
!!    the layer's percolation (sepday) and lateral flow (latlyr). The layer
!!    just above a water table is the bottom of the profile for the day, so
!!    its percolation is limited as SWAT+ limits the bottom layer's (by the
!!    HRU's perco_lim), the rest staying in it. A held layer (below the water
!!    table) gives back its percolation, and its lateral flow too unless
!!    started with --held-lateral=swat. In the top held layer, what is then
!!    above saturation (ul), the water that percolated in from above, fills
!!    the held layers below, bottom up, and the rest leaves the profile as
!!    SWAT+'s percolation (sepbtm, the recharge), counted as percolation out
!!    of every held layer it passes (soil%ly%prk), so its nitrate goes down
!!    with it. With the top layer held (the water table at the surface) the
!!    day's infiltration stays in it, and swr_satexcess sends what the
!!    profile can't hold to the surface: no recharge

      use soil_module, only : soil
      use hru_module, only : hru, sepday, latlyr, sepbtm

      integer, intent(in) :: j                            !HRU number
      integer, intent(in) :: j1                           !soil layer
      integer :: k = 0
      real :: over = 0.                                   !mm
      real :: add = 0.                                    !mm

      if (.not. exch_on) return
      if (j < lbound(hru_hly1,1) .or. j > ubound(hru_hly1,1)) return
      if (hru_hly1(j) == 0) return
      if (.not. hly_held(hru_hly1(j) + j1 - 1)) then
        if (j1 < soil(j)%nly) then
          if (hly_held(hru_hly1(j) + j1)) then
            add = sepday * hru(j)%hyd%perco_lim
            soil(j)%phys(j1)%st = soil(j)%phys(j1)%st + (sepday - add)
            sepday = add
          end if
        end if
        return
      end if
      soil(j)%phys(j1)%st = soil(j)%phys(j1)%st + sepday
      sepday = 0.
      if (.not. held_lat) then
        soil(j)%phys(j1)%st = soil(j)%phys(j1)%st + latlyr
        latlyr = 0.
      end if
      if (j1 == 1) return
      if (hly_held(hru_hly1(j) + j1 - 2)) return             !not the top held layer: nothing came in
      over = soil(j)%phys(j1)%st - soil(j)%phys(j1)%ul
      if (over <= 0.) return
      soil(j)%phys(j1)%st = soil(j)%phys(j1)%ul
      do k = soil(j)%nly, j1 + 1, -1
        add = min(over, max(0., soil(j)%phys(k)%ul - soil(j)%phys(k)%st))
        soil(j)%phys(k)%st = soil(j)%phys(k)%st + add
        over = over - add
      end do
      if (over <= 0.) return
      sepbtm(j) = sepbtm(j) + over
      do k = j1, soil(j)%nly
        soil(j)%ly(k)%prk = soil(j)%ly(k)%prk + over
      end do

      return
      end subroutine exch_hru_held

!!    --------------------------------------------------------------------
      subroutine exch_hru_satex (j, excess)

!!    called in swr_satexcess where water above saturation leaves the top of
!!    the profile, to the surface runoff or to a ponded HRU's water: adds it
!!    to the day's saturation excess, whatever gw_soil_flag is. Only adds up

      integer, intent(in) :: j                            !HRU number
      real, intent(in) :: excess                          !mm

      if (.not. exch_on) return
      if (.not. allocated(sat_day)) return
      if (j < 1 .or. j > size(sat_day)) return
      sat_day(j) = sat_day(j) + excess

      return
      end subroutine exch_hru_satex

!!    --------------------------------------------------------------------
      subroutine exch_hru_gwsoil_mass (j, k)

!!    called at the top of nut_nlch (k = 1, nitrate) and nut_solp (k = 2,
!!    labile phosphorus), right after gwflow adds what its groundwater
!!    brings to the soil: adds the mass set with the day's groundwater to
!!    each of the HRU's layers and counts it as SWAT+ counts gwflow's
!!    (gwsoiln, gwsoilp, printed in hru_nb)

      use organic_mineral_mass_module, only : soil1
      use soil_module, only : soil
      use hru_module, only : hru, gwsoiln, gwsoilp

      integer, intent(in) :: j                            !HRU number
      integer, intent(in) :: k                            !1 nitrate, 2 labile P
      integer :: ly = 0
      integer :: i = 0
      real :: add = 0.                                    !kg/ha

      if (.not. exch_on) return
      if (j < lbound(hru_hly1,1) .or. j > ubound(hru_hly1,1)) return
      if (hru_hly1(j) == 0) return
      do ly = 1, soil(j)%nly
        i = hru_hly1(j) + ly - 1
        if (.not. hly_gwm_on(k,i)) cycle
        add = hly_gwm(k,i) / hru(j)%area_ha               !kg/day -> kg/ha
        if (k == 1) then
          soil1(j)%mn(ly)%no3 = soil1(j)%mn(ly)%no3 + add
          gwsoiln(j) = gwsoiln(j) + add
        else
          soil1(j)%mp(ly)%lab = soil1(j)%mp(ly)%lab + add
          gwsoilp(j) = gwsoilp(j) + add
        end if
        hly_gwm_day(k,i) = hly_gwm(k,i)
        hly_gwm_on(k,i) = .false.
      end do

      return
      end subroutine exch_hru_gwsoil_mass

!!    --------------------------------------------------------------------
      subroutine exch_hru_irr (j)

!!    called in hru_control right after mgt_operatn, before surface: adds
!!    the irrigation set for the HRU's day to SWAT+'s own for the day, where
!!    SWAT+'s irrigation goes. Decision tables (actions, at the top of the
!!    HRU's day) and scheduled irrigation (mgt_operatn) set irrig(j)%applied
!!    and %runoff with "=" before this, so the set water adds to theirs.
!!    The water into the soil goes on irrig(j)%applied, which swr_percmain
!!    puts into the soil and hru_control prints as irr (hwb_d), and into the
!!    yearly sum decision tables test (hru(j)%irr_yr); the runoff goes on
!!    irrig(j)%runoff, which surface adds to the day's surface runoff. Both
!!    restart the days since the last irrigation, as SWAT+'s do. The
!!    nitrate and soluble P go into the top soil layer (soil1(j)%mn(1)%no3,
!!    %mp(1)%lab), where fertilizer goes. Not on a wetland or paddy HRU:
!!    SWAT+ puts irrigation there into the ponded water, and one that became
!!    a wetland during the day takes none (refused at the set otherwise).

      use hydrograph_module, only : irrig
      use hru_module, only : hru
      use plant_module, only : pcom
      use organic_mineral_mass_module, only : soil1

      integer, intent(in) :: j                            !HRU number
      real(8) :: m3mm = 0.d0                              !m3 per mm on the HRU
      real :: add = 0.                                    !mm or kg/ha

      if (.not. exch_on) return
      if (.not. allocated(ira_on)) return
      if (j < 1 .or. j > size(ira_on,2)) return
      if (.not. any(ira_on(:,j))) return
      if (hru(j)%dbs%surf_stor > 0 .or. hru(j)%area_ha <= 0.) then
        ira_on(:,j) = .false.
        return
      end if

      m3mm = 10.d0 * hru(j)%area_ha
      if (ira_on(1,j)) then
        add = real(ira_set(1,j) * 86400.d0 / m3mm)        !m3/s -> mm
        irrig(j)%applied = irrig(j)%applied + add
        hru(j)%irr_yr = hru(j)%irr_yr + add
        ira_day(1,j) = add * m3mm                         !m3/day
        if (add > 0.) pcom(j)%days_irr = 1
      end if
      if (ira_on(2,j)) then
        add = real(ira_set(2,j) * 86400.d0 / m3mm)
        irrig(j)%runoff = irrig(j)%runoff + add
        ira_day(2,j) = add * m3mm
        if (add > 0.) pcom(j)%days_irr = 1
      end if
      if (ira_on(3,j)) then
        add = real(ira_set(3,j) * 86400.d0 / hru(j)%area_ha)   !kg/s -> kg/ha
        soil1(j)%mn(1)%no3 = soil1(j)%mn(1)%no3 + add
        ira_day(3,j) = add * hru(j)%area_ha                    !kg/day
      end if
      if (ira_on(4,j)) then
        add = real(ira_set(4,j) * 86400.d0 / hru(j)%area_ha)
        soil1(j)%mp(1)%lab = soil1(j)%mp(1)%lab + add
        ira_day(4,j) = add * hru(j)%area_ha
      end if
      ira_on(:,j) = .false.

      return
      end subroutine exch_hru_irr

!!    --------------------------------------------------------------------
      subroutine exch_hru_cover (j, cover)

!!    called in et_act right after SWAT+ computes the HRU's soil cover
!!    (above-ground plant mass of the community plus surface residue,
!!    pl_mass(j)%ab_gr_com%m + pl_mass(j)%rsd_tot%m, kg/ha) and before its
!!    soil evaporation uses it (eaj = Exp(-5.e-5 * (cover + 0.1))): a cover
!!    set for the day replaces SWAT+'s, and the cover used is kept for the
!!    read. et_act computes the cover only when the canopy's storage leaves
!!    some of the day's PET (pet > 1.e-6), so on other days this isn't
!!    called and a cover set for the day isn't used. Under snow (sno_mm >=
!!    0.5) SWAT+ takes eaj = 0.5 whatever the cover, and on a wetland with
!!    water its evaporation doesn't use the cover; a set cover is used there
!!    as SWAT+'s own would be, which is not at all.

      integer, intent(in) :: j                            !HRU number
      real, intent(inout) :: cover                        !kg/ha

      if (.not. exch_on) return
      if (.not. allocated(cov_on)) return
      if (j < 1 .or. j > size(cov_on)) return
      if (cov_on(j)) then
        cover = cov_set(j)
        cov_on(j) = .false.
      end if
      cov_day(j) = cover

      return
      end subroutine exch_hru_cover

!!    --------------------------------------------------------------------
      real function rating_depth (ich, flo_rate)

!!    water depth on SWAT+'s rating curve for a channel at a flow rate (m3/s),
!!    using SWAT+'s own interpolation; SWAT+'s working rating curve (rcurv) is
!!    put back as it was, so reading changes nothing

      use sd_channel_module, only : rcurv, channel_rating_curve_parameters

      integer, intent(in) :: ich
      real, intent(in) :: flo_rate
      type (channel_rating_curve_parameters) :: keep
      external :: rcurv_interp_flo

      if (flo_rate <= 0.) then
        rating_depth = 0.
        return
      end if
      keep = rcurv
      call rcurv_interp_flo (ich, flo_rate)
      rating_depth = rcurv%dep
      rcurv = keep

      return
      end function rating_depth

!!    --------------------------------------------------------------------
      subroutine exch_record

!!    called in command after the command loop and before the outputs are
!!    printed: keeps the day's values that printing zeroes

      use water_allocation_module, only : poud_met
      use hydrograph_module, only : irrig

      integer :: i = 0

      if (.not. exch_on) return

      do i = 1, size(pou_wdr_day)
        pou_dmd_day(i) = poud_met(i)%duty_tot%duty
        pou_wdr_day(i) = poud_met(i)%duty_tot%deliv
      end do
      !! the day's irrigation, zeroed at the start of the next day before the pause:
      !! what reached the soil and what ran off, as SWAT+'s water balance takes it
      do i = 1, size(hru_irr_day)
        hru_irr_day(i) = irrig(i)%applied + irrig(i)%runoff
      end do
      !! what irrigate actions took from channels and reservoirs since the last record
      !! (exch_irr_take); the next day's adds up from zero
      irr_cha_day = irr_cha_now
      irr_res_day = irr_res_now
      irr_hru_day = irr_hru_now
      irr_cha_now = 0.d0
      irr_res_now = 0.d0
      irr_hru_now = 0.d0
      !! a state or flux set for the day applies that day only, even for an
      !! HRU the day's commands didn't run
      hly_on = .false.
      hly_gw_on = .false.
      hly_gwm_on = .false.
      ira_on = .false.
      cov_on = .false.

      return
      end subroutine exch_record

!!    --------------------------------------------------------------------
      subroutine exch_end

!!    called in time_control after the year loop: reports the end and
!!    waits until told to finish

      if (.not. exch_on) return

      if (loading) then
        call reply ("@error the simulation ended before the saved day " // itoa(load_day))
        error stop 6
      end if
      ended = .true.
      call reply ("@ended " // itoa(days_done))
      if (.not. run_free) call serve (.true.)

      return
      end subroutine exch_end

!!    --------------------------------------------------------------------
      subroutine serve (at_end)

!!    reads and answers commands until one lets the simulation go on

      logical, intent(in) :: at_end
      character(len=4096) :: line = ""
      character(len=64) :: word = ""
      character(len=1024) :: name = ""
      character(len=1024) :: path = ""
      integer :: ios = 0
      integer :: n = 0
      integer :: i = 0
      integer :: j = 0
      integer :: k = 0
      real(8) :: v = 0.d0
      real(8), dimension(:), allocatable :: val

      do
        read (cmd_unit, '(a)', iostat=ios) line
        if (ios /= 0) then
          !! the caller is gone: stop where we are
          error stop 3
        end if
        word = ""
        read (line, *, iostat=ios) word

        select case (trim(word))

        case ("run")
          n = 1
          read (line, *, iostat=ios) word, n
          if (ios /= 0) n = 1
          if (at_end) then
            call reply ("@error the simulation has ended; send finish")
          else if (n < 1) then
            call reply ("@error run needs a day count of 1 or more")
          else
            run_left = n
            return
          end if

        case ("finish")
          !! run to the end without pausing and let SWAT+ complete
          run_free = .true.
          return

        case ("get")
          read (line, *, iostat=ios) word, name
          if (ios /= 0) then
            call reply ("@error get needs a variable name")
          else
            call send_values (trim(name))
          end if

        case ("geti")
          read (line, *, iostat=ios) word, name, n
          if (ios /= 0 .or. n < 0) then
            call reply ("@error geti needs a variable name and an index count")
          else
            call send_at (trim(name), n)
          end if

        case ("set")
          read (line, *, iostat=ios) word, name, i, v
          if (ios /= 0) then
            call reply ("@error set needs a variable name, a 1-based index and a value")
          else if (at_end) then
            call reply ("@error the simulation has ended; nothing left to set")
          else
            call set_value (trim(name), i, v)
          end if

        !! a command with an array to follow is refused before @send, so the caller
        !! sends no bytes; after @send it sends exactly the count given
        case ("setv", "seti")
          read (line, *, iostat=ios) word, name, n
          if (ios /= 0 .or. n < 0) then
            call reply ("@error " // trim(word) // " needs a variable name and a value count")
          else if (at_end) then
            call reply ("@error the simulation has ended; nothing left to set")
          else
            call set_values (trim(name), n, trim(word) == "seti")
          end if

        case ("setr")
          read (line, *, iostat=ios) word, name, i, n, k, j
          if (ios /= 0 .or. k < 0 .or. j < 0) then
            call reply ("@error setr needs a variable name, a year, a day of the year, a day count " // &
                        "and a gage count")
          else if (at_end) then
            call reply ("@error the simulation has ended; nothing left to set")
          else
            call set_records (trim(name), i, n, k, j)
          end if

        case ("ping")
          call reply ("@ok")

        case ("echo")
          !! the array sent, sent back: a check of the channel itself
          read (line, *, iostat=ios) word, n
          if (ios /= 0 .or. n < 0) then
            call reply ("@error echo needs a value count")
          else
            allocate (val(n))
            call reply ("@send")
            call get_bytes (val, 8 * size(val, kind=c_size_t))
            call send_array (val)
            deallocate (val)
          end if

        case ("flush")
          call flush_units
          call reply ("@ok")

        case ("save")
          read (line, *, iostat=ios) word, path, name
          if (ios /= 0) then
            call reply ('@error save needs "<file>" "<run dir>"')
          else
            call save_state (trim(path), trim(name))
          end if

        case ("load")
          read (line, *, iostat=ios) word, path
          if (ios /= 0) then
            call reply ('@error load needs "<file>"')
          else if (at_end .or. days_done > 0) then
            call reply ("@error load only right after startup, before the first day has run")
          else
            call start_load (trim(path))
            if (loading) return
          end if

        case ("branch")
          if (at_end) then
            call reply ("@error the simulation has ended; nothing left to branch")
          else
            call branch (line)
          end if

        case ("stop")
          !! stop now, without completing the run
          call reply ("@bye")
          stop

        case default
          call reply ("@error unknown command: " // trim(word))

        end select
      end do

      end subroutine serve

!!    --------------------------------------------------------------------
      subroutine flush_units

!!    writes out everything SWAT+ has buffered, so the files on disk are
!!    complete up to the pause

      integer :: u = 0
      logical :: op = .false.

      do u = 1, unit_max
        inquire (unit=u, opened=op)
        if (op) flush (u)
      end do

      return
      end subroutine flush_units

!!    --------------------------------------------------------------------
      subroutine branch (line)

!!    branch "<new dir>" "<old dir>" "<command fifo>" "<reply fifo>"
!!
!!    duplicates this paused process with fork. The caller first sends
!!    flush and copies the run folder to <new dir>. The parent replies
!!    @branched <pid> and goes on as before. The child moves to <new dir>,
!!    reopens its open files there at the same place, talks over the two
!!    named pipes, and replies there with the same @paused line. Mac and
!!    Linux only; on Windows the wrapper branches by saving and loading.

      character(len=*), intent(in) :: line
#if defined(_WIN32)
      call reply ("@error branch needs fork, which Windows doesn't have")
#else
      interface
        function swx_fork () bind(c, name="swx_fork")
          import :: c_int
          integer(c_int) :: swx_fork
        end function swx_fork
        function swx_chdir (path) bind(c, name="swx_chdir")
          import :: c_int, c_char
          character(kind=c_char), dimension(*), intent(in) :: path
          integer(c_int) :: swx_chdir
        end function swx_chdir
        function swx_detach (path) bind(c, name="swx_detach")
          import :: c_int, c_char
          character(kind=c_char), dimension(*), intent(in) :: path
          integer(c_int) :: swx_detach
        end function swx_detach
      end interface

      character(len=16) :: word = ""
      character(len=1024) :: new_dir = ""
      character(len=1024) :: old_dir = ""
      character(len=1024) :: fifo_in = ""
      character(len=1024) :: fifo_out = ""
      character(len=1200) :: msg = ""
      integer :: ios = 0
      integer :: pid = 0

      read (line, *, iostat=ios) word, new_dir, old_dir, fifo_in, fifo_out
      if (ios /= 0) then
        call reply ('@error branch needs "<new dir>" "<old dir>" "<command fifo>" "<reply fifo>"')
        return
      end if
      call check_units (trim(old_dir), msg)
      if (msg /= "") then
        call reply ("@error can't branch: " // trim(msg))
        return
      end if

      call flush_units
      pid = swx_fork ()
      if (pid < 0) then
        call reply ("@error fork failed")
        return
      end if
      if (pid > 0) then
        call reply ("@branched " // itoa(pid))
        return
      end if

      !! the child: leave the parent's channels and folder. Its copies of the
      !! array channels go too, or the parent's caller would never see the end
      !! of the parent's replies
      call swx_close (bin_in)
      call swx_close (bin_out)
      if (swx_chdir (trim(new_dir) // c_null_char) /= 0) error stop 4
      ios = swx_detach ("exchange.err" // c_null_char)
      open (6, file="exchange.log", action="write", position="append")
      write (6,*) "branched from ", trim(old_dir), " after day ", days_done
      call close_units (trim(old_dir))
      call reopen_closed
      !! in the order the caller opens them: commands, then replies
      open (newunit=cmd_unit, file=trim(fifo_in), action="read", status="old")
      bin_in = swx_open (trim(fifo_in) // c_null_char, 0_c_int)
      bin_out = swx_open (trim(fifo_out) // c_null_char, 1_c_int)
      if (bin_in < 0 .or. bin_out < 0) error stop 4
      call reply (paused_line)
#endif

      return
      end subroutine branch

!!    --------------------------------------------------------------------
      subroutine check_units (run_dir, msg)

!!    the open files a branch or a saved state can't carry, for an @error:
!!    a file outside the run folder (an out_path in file.cio that leads
!!    out of it), which the copy of the run folder doesn't have, and a
!!    file partway through, which can't be opened again at the same place
!!    (gwflow reads chan_depth.gw and pond_div.gw a day at a time). msg is
!!    blank when there are none.

      character(len=*), intent(in) :: run_dir
      character(len=*), intent(out) :: msg
      character(len=1024) :: nm = ""
      character(len=16) :: acc = ""
      character(len=16) :: pos = ""
      integer :: u = 0
      integer :: n = 0
      integer(8) :: sz = 0
      logical :: op = .false.
      logical :: nd = .false.

      msg = ""
      n = len_trim(run_dir)
      do u = 1, unit_max
        if (u == 5 .or. u == 6) cycle
        inquire (unit=u, opened=op)
        if (.not. op) cycle
        inquire (unit=u, named=nd, name=nm, access=acc, position=pos, size=sz)
        if (.not. nd) cycle
        !! a path from the root or a drive (SWAT+ writes out_path with \ on Windows)
        if (nm(1:1) == "/" .or. nm(1:1) == "\" .or. nm(2:2) == ":") then
          if (.not. (len_trim(nm) > n + 1 .and. slashed(nm(1:n)) == slashed(run_dir(1:n)) .and. &
                     slashed(nm(n+1:n+1)) == "/")) then
            msg = trim(nm) // " is outside the run folder (out_path in file.cio?)"
            return
          end if
        else if (slashed(nm(1:3)) == "../") then
          msg = trim(nm) // " is outside the run folder (out_path in file.cio?)"
          return
        end if
        if (pos == "ASIS" .and. sz == 0) pos = "APPEND"
        if (acc /= "DIRECT" .and. pos /= "APPEND" .and. pos /= "REWIND") then
          msg = trim(nm) // " is open partway through, and SWAT+ reads it again later"
          return
        end if
      end do

      return
      end subroutine check_units

!!    --------------------------------------------------------------------
      subroutine close_units (old_dir)

!!    closes every file SWAT+ has open and remembers how to open it again at
!!    the same place (start or end): for a branch, the copy in the branch's
!!    folder; for a load, the saved copy put back in the run folder. A file
!!    left partway through reading can't be placed again and is left open;
!!    exchange.log says so. Paths in old_dir become relative.

      character(len=*), intent(in) :: old_dir
      character(len=1024) :: nm = ""
      character(len=16) :: acc = ""
      character(len=16) :: frm = ""
      character(len=16) :: act = ""
      character(len=16) :: pos = ""
      integer :: u = 0
      integer :: rl = 0
      integer :: n = 0
      integer(8) :: sz = 0
      logical :: op = .false.
      logical :: nd = .false.

      if (allocated(closed)) deallocate (closed)
      allocate (closed(0))
      n_closed = 0
      n = len_trim(old_dir)
      do u = 1, unit_max
        if (u == 5 .or. u == 6) cycle
        inquire (unit=u, opened=op)
        if (.not. op) cycle
        inquire (unit=u, named=nd, name=nm, access=acc, form=frm, recl=rl, action=act, position=pos, &
                 size=sz)
        if (.not. nd) cycle
        !! a file opened but not yet written is at its start, which is also its end
        if (pos == "ASIS" .and. sz == 0) pos = "APPEND"
        if (n > 0 .and. len_trim(nm) > n + 1) then
          if (slashed(nm(1:n)) == slashed(old_dir(1:n)) .and. slashed(nm(n+1:n+1)) == "/") nm = nm(n+2:)
        end if
        if (acc /= "DIRECT" .and. pos /= "APPEND" .and. pos /= "REWIND") then
          write (6,*) "unit ", u, " (", trim(nm), ") is partway through; left open"
          cycle
        end if
        close (u)
        closed = [closed, unit_file(u, nm, acc, frm, act, pos, rl)]
        n_closed = n_closed + 1
      end do

      return
      end subroutine close_units

!!    --------------------------------------------------------------------
      subroutine reopen_closed

      integer :: i = 0
      integer :: ios = 0

      do i = 1, n_closed
        associate (c => closed(i))
        if (c%acc == "STREAM") then
          open (c%u, file=trim(c%name), access="stream", form=trim(c%frm), action=trim(c%act),  &
                position=trim(c%pos), status="old", iostat=ios)
        else if (c%acc == "DIRECT") then
          open (c%u, file=trim(c%name), access="direct", form=trim(c%frm), recl=c%rl,           &
                action=trim(c%act), status="old", iostat=ios)
        else
          open (c%u, file=trim(c%name), access="sequential", form=trim(c%frm), recl=c%rl,       &
                action=trim(c%act), position=trim(c%pos), status="old", iostat=ios)
        end if
        if (ios /= 0) write (6,*) "unit ", c%u, " (", trim(c%name), ") could not be opened again"
        end associate
      end do

      return
      end subroutine reopen_closed

!!    --------------------------------------------------------------------
      subroutine save_state (path, run_dir)

!!    save "<file>" "<run dir>": writes all of SWAT+'s module state, as it
!!    is at this pause, to <file> (bmi_state_module, generated per release),
!!    with the values set for the next day. The caller keeps a copy of the
!!    run folder with it; output files are flushed first so that copy is
!!    complete.

      use bmi_state_module, only : swx_state_save, swx_state_digest

      character(len=*), intent(in) :: path
      character(len=*), intent(in) :: run_dir
      character(len=1200) :: msg = ""
      integer :: u = 0
      integer :: ios = 0

      call check_units (run_dir, msg)
      if (msg /= "") then
        call reply ("@error can't save: " // trim(msg))
        return
      end if
      call flush_units
      open (newunit=u, file=path, access="stream", form="unformatted", action="write",          &
            status="replace", iostat=ios)
      if (ios /= 0) then
        call reply ("@error can't write " // path)
        return
      end if
      write (u) state_magic, swx_state_digest, days_done, paused_line
      write (u) size(pou_dmd_day), pou_dmd_day, pou_wdr_day
      write (u) size(hru_irr_day), hru_irr_day
      write (u) size(irr_cha_now), size(irr_res_now), irr_cha_now, irr_cha_day, irr_res_now,   &
                irr_res_day, irr_hru_now, irr_hru_day
      write (u) size(ira_set,2), ira_set, ira_on, ira_day
      write (u) size(cov_set), cov_set, cov_on, cov_day
      write (u) n_hly, size(sat_day), hly_held, held_lat, sat_day, nt_day
      write (u) size(chx_day), chx_day
      write (u) size(rsx_flo), rsx_flo, rsx_on, rsv_day
      write (u) size(rec_flo), rec_flo, rec_flo_on, dmd_flo, dmd_flo_on, chx_flo, chx_on
      write (u) n_hly, hly_tmp, hly_on, hly_gw, hly_gw_on, hly_gw_day, hly_gwm, hly_gwm_on, hly_gwm_day
      write (u) n_sp, chm_flo, chm_on, chm_day, chm_wait, rcm_flo, rcm_on
      call save_units (u, run_dir)
      call swx_state_save (u)
      write (u) state_magic
      close (u)
      call reply ("@ok")

      return
      end subroutine save_state

!!    --------------------------------------------------------------------
      subroutine save_units (u, run_dir)

!!    the files open at this pause, for a load: SWAT+ opens some output
!!    files only when it first writes to them (salt_balance writes units
!!    5080-5086 that way, as fort.<unit>), so a process loading the state
!!    may not have them open yet, and its first write would start the
!!    restored file again from the top. Paths in run_dir become relative.

      integer, intent(in) :: u
      character(len=*), intent(in) :: run_dir
      character(len=1024) :: nm = ""
      character(len=16) :: acc = ""
      character(len=16) :: frm = ""
      character(len=16) :: act = ""
      integer :: k = 0
      integer :: n = 0
      integer :: m = 0
      integer :: rl = 0
      logical :: op = .false.
      logical :: nd = .false.

      do m = 1, 2                                         !count, then write
        if (m == 2) write (u) n
        n = 0
        do k = 1, unit_max
          if (k == 5 .or. k == 6 .or. k == u .or. k == cmd_unit) cycle
          inquire (unit=k, opened=op)
          if (.not. op) cycle
          inquire (unit=k, named=nd, name=nm, access=acc, form=frm, action=act, recl=rl)
          if (.not. nd .or. acc == "DIRECT" .or. index(act, "WRITE") == 0) cycle
          n = n + 1
          if (m == 2) then
            if (len_trim(nm) > len_trim(run_dir) + 1) then
              if (nm(1:len_trim(run_dir)) == run_dir .and. nm(len_trim(run_dir)+1:len_trim(run_dir)+1) == "/") &
                nm = nm(len_trim(run_dir)+2:)
            end if
            write (u) unit_file(k, nm, acc, frm, act, "APPEND", rl)
          end if
        end do
      end do

      return
      end subroutine save_units

!!    --------------------------------------------------------------------
      subroutine read_units (u)

      integer, intent(in) :: u
      integer :: n = 0
      integer :: k = 0

      read (u) n
      if (allocated(saved_units)) deallocate (saved_units)
      allocate (saved_units(n))
      do k = 1, n
        read (u) saved_units(k)
      end do

      return
      end subroutine read_units

!!    --------------------------------------------------------------------
      subroutine open_saved_units

!!    after a load: opens, at its end, each output file the saving process
!!    had open that this one hasn't opened yet (save_units)

      integer :: k = 0
      integer :: ios = 0
      logical :: op = .false.

      if (.not. allocated(saved_units)) return
      do k = 1, size(saved_units)
        associate (c => saved_units(k))
        inquire (unit=c%u, opened=op)
        if (op) cycle
        if (c%rl > 0) then
          open (c%u, file=trim(c%name), access=trim(c%acc), form=trim(c%frm), action=trim(c%act),  &
                recl=c%rl, position="append", status="old", iostat=ios)
        else
          open (c%u, file=trim(c%name), access=trim(c%acc), form=trim(c%frm), action=trim(c%act),  &
                position="append", status="old", iostat=ios)
        end if
        if (ios /= 0) write (6,*) "unit ", c%u, " (", trim(c%name), ") could not be opened again"
        end associate
      end do

      return
      end subroutine open_saved_units

!!    --------------------------------------------------------------------
      subroutine start_load (path)

!!    load "<file>", right after startup: checks the file, then lets SWAT+
!!    run forward to the saved day without computing it (exch_pause), where
!!    load_now puts the saved state in place

      use bmi_state_module, only : swx_state_digest

      character(len=*), intent(in) :: path
      character(len=16) :: magic = ""
      character(len=16) :: digest = ""
      integer :: u = 0
      integer :: ios = 0

      open (newunit=u, file=path, access="stream", form="unformatted", action="read",           &
            status="old", iostat=ios)
      if (ios /= 0) then
        call reply ("@error can't read " // path)
        return
      end if
      read (u, iostat=ios) magic, digest, load_day
      close (u)
      if (ios /= 0 .or. (magic /= state_magic .and. magic /= state_magic_9 .and. magic /= state_magic_8 .and.    &
          magic /= state_magic_7 .and. magic /= state_magic_6)) then
        call reply ("@error not a saved SWAT+ state: " // path)
      else if (digest /= swx_state_digest) then
        call reply ("@error the state was saved by a different build (" // trim(digest) //         &
                    "; this build is " // swx_state_digest // ")")
      else if (load_day > days_total) then
        call reply ("@error the saved day is past the end of this simulation")
      else
        loading = .true.
        load_path = path
        run_left = 0
        run_free = .false.
      end if

      return
      end subroutine start_load

!!    --------------------------------------------------------------------
      subroutine load_now

!!    at the saved day: reads the saved state over SWAT+'s, with the values
!!    set for the next day, closes the output files, asks the caller to put
!!    the saved ones back (@restore, answered with "restored"), and opens
!!    them again at their ends

      use bmi_state_module, only : swx_state_load

      character(len=16) :: magic = ""
      character(len=16) :: head = ""                      !! the state's version, from its first line
      character(len=16) :: digest = ""
      character(len=64) :: word = ""
      integer :: u = 0
      integer :: n = 0
      integer :: k = 0
      integer :: ios = 0

      open (newunit=u, file=trim(load_path), access="stream", form="unformatted", action="read",  &
            status="old")
      read (u) magic, digest, days_done, paused_line
      read (u) n
      if (n /= size(pou_dmd_day)) then
        call reply ("@error the saved state has a different number of places of use")
        error stop 7
      end if
      read (u) pou_dmd_day, pou_wdr_day
      read (u) n
      if (n /= size(hru_irr_day)) then
        call reply ("@error the saved state has a different number of HRUs")
        error stop 7
      end if
      read (u) hru_irr_day
      if (magic == state_magic_6) then
        !! saved by 0.13, before the irrigation withdrawals were counted: they start from zero.
        !! What the saved day's decision tables took before the pause isn't in that state
        irr_cha_now = 0.d0; irr_cha_day = 0.d0; irr_res_now = 0.d0; irr_res_day = 0.d0
        irr_hru_now = 0.d0; irr_hru_day = 0.d0
      else
        read (u) n, k
        if (n /= size(irr_cha_now) .or. k /= size(irr_res_now)) then
          call reply ("@error the saved state has a different number of channels or reservoirs")
          error stop 7
        end if
        read (u) irr_cha_now, irr_cha_day, irr_res_now, irr_res_day, irr_hru_now, irr_hru_day
      end if
      if (magic == state_magic_7 .or. magic == state_magic_6) then
        !! saved by 0.14 or 0.13, before irrigation could be applied from outside: none is set
        ira_set = 0.d0; ira_on = .false.; ira_day = 0.d0
      else
        read (u) n
        if (n /= size(ira_set,2)) then
          call reply ("@error the saved state has a different number of HRUs")
          error stop 7
        end if
        read (u) ira_set, ira_on, ira_day
      end if
      if (magic == state_magic_8 .or. magic == state_magic_7 .or. magic == state_magic_6) then
        !! saved by 0.15, 0.14 or 0.13, before the soil cover could be set: none is set, and the
        !! saved day's cover reads as none computed
        cov_set = 0.; cov_on = .false.; cov_day = ieee_value (0., ieee_quiet_nan)
      else
        read (u) n
        if (n /= size(cov_set)) then
          call reply ("@error the saved state has a different number of HRUs")
          error stop 7
        end if
        read (u) cov_set, cov_on, cov_day
      end if
      if (magic == state_magic_9 .or. magic == state_magic_8 .or. magic == state_magic_7 .or.       &
          magic == state_magic_6) then
        !! saved by 0.16 or before, with no water table: no layer is held, and the option is
        !! the one this process was started with
        hly_held = .false.; sat_day = 0.; nt_day = 0.
      else
        read (u) n, k
        if (n /= n_hly .or. k /= size(sat_day)) then
          call reply ("@error the saved state has a different number of HRUs or soil layers")
          error stop 7
        end if
        read (u) hly_held, held_lat, sat_day, nt_day
      end if
      read (u) n
      if (n /= size(chx_day)) then
        call reply ("@error the saved state has a different number of channels")
        error stop 7
      end if
      read (u) chx_day
      read (u) n
      if (n /= size(rsx_flo)) then
        call reply ("@error the saved state has a different number of reservoirs")
        error stop 7
      end if
      read (u) rsx_flo, rsx_on, rsv_day
      read (u) n
      if (n /= size(rec_flo)) then
        call reply ("@error the saved state has a different number of point sources")
        error stop 7
      end if
      read (u) rec_flo, rec_flo_on, dmd_flo, dmd_flo_on, chx_flo, chx_on
      read (u) n
      if (n /= n_hly) then
        call reply ("@error the saved state has a different number of soil layers")
        error stop 7
      end if
      read (u) hly_tmp, hly_on, hly_gw, hly_gw_on, hly_gw_day, hly_gwm, hly_gwm_on, hly_gwm_day
      read (u) n
      if (n /= n_sp) then
        call reply ("@error the saved state has a different number of salt ions")
        error stop 7
      end if
      read (u) chm_flo, chm_on, chm_day, chm_wait, rcm_flo, rcm_on
      call read_units (u)
      call swx_state_load (u)
      head = magic
      read (u) magic
      close (u)
      if (magic /= head) then
        call reply ("@error the saved state ends early: " // trim(load_path))
        error stop 7
      end if
      loading = .false.
      run_left = 0
      run_free = .false.

      call flush_units
      call close_units ("")
      call reply ("@restore")
      read (cmd_unit, *, iostat=ios) word
      if (ios /= 0 .or. trim(word) /= "restored") error stop 3
      call reopen_closed
      call open_saved_units

      return
      end subroutine load_now

!!    --------------------------------------------------------------------
      subroutine send_values (name)

!!    get <var>: @bin n, then the n values

      character(len=*), intent(in) :: name
      real(8), dimension(:), allocatable :: val

      if (values_of (name, val)) call send_array (val)

      return
      end subroutine send_values

!!    --------------------------------------------------------------------
      subroutine send_at (name, n)

!!    geti <var> <n>: @send, then n 4-byte 1-based indices; @bin n, then the
!!    values at those indices, in the order given

      character(len=*), intent(in) :: name
      integer, intent(in) :: n
      real(8), dimension(:), allocatable :: val
      integer(c_int32_t), dimension(:), allocatable :: idx
      integer :: k = 0

      if (var_index(name) == 0) then
        call reply ("@error unknown variable: " // name)
        return
      end if
      allocate (idx(n))
      call reply ("@send")
      call get_bytes (idx, 4 * size(idx, kind=c_size_t))
      if (.not. values_of (name, val)) return
      do k = 1, n
        if (idx(k) < 1 .or. idx(k) > size(val)) then
          call reply ("@error index " // itoa(k) // " is " // itoa(int(idx(k))) // ", not 1 to " // &
                      itoa(size(val)))
          return
        end if
      end do
      call send_array (val(idx))

      return
      end subroutine send_at

!!    --------------------------------------------------------------------
      logical function values_of (name, val)

!!    a variable's values, one per object on its grid; false, after an
!!    @error, for an unknown variable

      use hydrograph_module, only : sp_ob, sp_ob1, ob, ch_in_d, ch_out_d, ch_stor, res
      use aquifer_module, only : aqu_d
      use maximum_data_module, only : db_mx
      use soil_module, only : soil
      use hru_module, only : hru
      use output_landscape_module, only : hwb_d, hnb_d
      use water_body_module, only : res_wat_d
      use organic_mineral_mass_module, only : pl_mass

      character(len=*), intent(in) :: name
      real(8), dimension(:), allocatable, intent(out) :: val
      integer :: i = 0
      integer :: iob = 0
      integer :: iv = 0
      integer :: k = 0

      select case (name)

      case ("channel_water__volume_outflow_rate")
        allocate (val(sp_ob%chandeg), source = 0.d0)
        do i = 1, sp_ob%chandeg
          iob = sp_ob1%chandeg + i - 1
          val(i) = ch_out_d(ob(iob)%num)%flo              !m3/s
        end do

      case ("channel_water__volume")
        allocate (val(sp_ob%chandeg), source = 0.d0)
        do i = 1, sp_ob%chandeg
          iob = sp_ob1%chandeg + i - 1
          val(i) = ch_stor(ob(iob)%num)%flo               !m3
        end do

      case ("channel_water__volume_inflow_rate")
        !! net inflow; negative when a point source diversion is larger than
        !! the other inflow, and SWAT+ then routes nothing
        allocate (val(sp_ob%chandeg), source = 0.d0)
        do i = 1, sp_ob%chandeg
          iob = sp_ob1%chandeg + i - 1
          val(i) = ch_in_d(ob(iob)%num)%flo               !m3/s
        end do

      case ("channel_water__depth")
        !! depth on SWAT+'s own rating curve at the day's mean outflow
        allocate (val(sp_ob%chandeg), source = 0.d0)
        do i = 1, sp_ob%chandeg
          iob = sp_ob1%chandeg + i - 1
          val(i) = rating_depth (ob(iob)%num, ch_out_d(ob(iob)%num)%flo)
        end do

      case ("channel_water__groundwater_volume_exchange_rate")
        !! what was exchanged on the day just done, positive into the channel
        allocate (val(sp_ob%chandeg), source = 0.d0)
        do i = 1, sp_ob%chandeg
          val(i) = chx_day(i) / 86400.d0                  !m3 -> m3/s
        end do

      case ("soil_layer__temperature")
        !! each HRU soil layer at the end of the day just done
        allocate (val(n_hly), source = 0.d0)
        do i = 1, n_hly
          val(i) = soil(hly_hru(i))%phys(hly_lay(i))%tmp   !deg C
        end do

      case ("soil_layer_water__groundwater_inflow_rate")
        !! what went into each HRU soil layer on the day just done
        allocate (val(n_hly), source = 0.d0)
        do i = 1, n_hly
          val(i) = hly_gw_day(i) / 86400000.d0            !mm/day -> m/s
        end do

      case ("soil_layer_bottom__depth", "soil_layer__thickness", "soil_layer_water__depth",           &
            "soil_layer_water__wilting_point_depth", "soil_layer_water__field_capacity_depth",       &
            "soil_layer_water__saturation_depth", "soil_layer__saturated_hydraulic_conductivity")
        !! each HRU soil layer as SWAT+ keeps it at the end of the day just done; the water
        !! depths are above the wilting point, as SWAT+ counts them (st, fc, ul), but wpmm
        allocate (val(n_hly), source = 0.d0)
        do i = 1, n_hly
          associate (ly => soil(hly_hru(i))%phys(hly_lay(i)))
          select case (name)
          case ("soil_layer_bottom__depth");               val(i) = ly%d / 1000.d0      !mm -> m
          case ("soil_layer__thickness");                  val(i) = ly%thick / 1000.d0
          case ("soil_layer_water__depth");                val(i) = ly%st / 1000.d0
          case ("soil_layer_water__wilting_point_depth");  val(i) = ly%wpmm / 1000.d0
          case ("soil_layer_water__field_capacity_depth"); val(i) = ly%fc / 1000.d0
          case ("soil_layer_water__saturation_depth");     val(i) = ly%ul / 1000.d0
          case default;                                    val(i) = ly%k / 3600000.d0   !mm/hr -> m/s
          end select
          end associate
        end do

      case ("soil_layer_water_no3__groundwater_inflow_mass_rate", "soil_layer_water_solp__groundwater_inflow_mass_rate")
        !! the mass that went into each HRU soil layer with the day's groundwater
        k = 1
        if (name(18:21) == "solp") k = 2
        allocate (val(n_hly), source = 0.d0)
        do i = 1, n_hly
          val(i) = hly_gwm_day(k,i) / 86400.d0             !kg/day -> kg/s
        end do

      case ("soil_water_no3__groundwater_inflow_mass_rate", "soil_water_solp__groundwater_inflow_mass_rate")
        !! the day's nitrate or labile P into the soil profile with groundwater, gwflow's and set
        allocate (val(sp_ob%hru), source = 0.d0)
        do i = 1, sp_ob%hru
          iob = sp_ob1%hru + i - 1
          if (name(12:14) == "no3") then
            val(i) = hnb_d(ob(iob)%num)%gwsoiln * hru(ob(iob)%num)%area_ha / 86400.d0   !kg/ha/day -> kg/s
          else
            val(i) = hnb_d(ob(iob)%num)%gwsoilp * hru(ob(iob)%num)%area_ha / 86400.d0
          end if
        end do

      case ("soil_water__groundwater_inflow_rate")
        !! the day's groundwater into the soil profile, gwflow's and set (gwsoilq)
        allocate (val(sp_ob%hru), source = 0.d0)
        do i = 1, sp_ob%hru
          iob = sp_ob1%hru + i - 1
          val(i) = hwb_d(ob(iob)%num)%gwsoil / 86400000.d0 !mm/day -> m/s
        end do

      case ("snowpack__liquid-equivalent_depth")
        allocate (val(sp_ob%hru), source = 0.d0)
        do i = 1, sp_ob%hru
          iob = sp_ob1%hru + i - 1
          val(i) = hru(ob(iob)%num)%sno_mm / 1000.d0       !mm -> m
        end do

      case ("soil_water__percolation_rate")
        !! the day's water leaving the bottom of the soil profile (sepbtm)
        allocate (val(sp_ob%hru), source = 0.d0)
        do i = 1, sp_ob%hru
          iob = sp_ob1%hru + i - 1
          val(i) = hwb_d(ob(iob)%num)%perc / 86400000.d0   !mm/day -> m/s
        end do

      case ("land_surface__evapotranspiration_rate")
        allocate (val(sp_ob%hru), source = 0.d0)
        do i = 1, sp_ob%hru
          iob = sp_ob1%hru + i - 1
          val(i) = hwb_d(ob(iob)%num)%et / 86400000.d0     !mm/day -> m/s
        end do

      case ("land_surface__potential_evapotranspiration_rate")
        allocate (val(sp_ob%hru), source = 0.d0)
        do i = 1, sp_ob%hru
          iob = sp_ob1%hru + i - 1
          val(i) = hwb_d(ob(iob)%num)%pet / 86400000.d0    !mm/day -> m/s
        end do

      case ("soil_water__depth")
        !! the profile's water above the wilting point at the end of the day
        allocate (val(sp_ob%hru), source = 0.d0)
        do i = 1, sp_ob%hru
          iob = sp_ob1%hru + i - 1
          val(i) = soil(ob(iob)%num)%sw / 1000.d0          !mm -> m
        end do

      case ("aquifer_water__volume")
        allocate (val(sp_ob%aqu), source = 0.d0)
        do i = 1, sp_ob%aqu
          iob = sp_ob1%aqu + i - 1
          val(i) = 10.d0 * aqu_d(ob(iob)%num)%stor * ob(iob)%area_ha   !mm -> m3
        end do

      case ("soil_water_table_surface__depth")
        allocate (val(sp_ob%aqu), source = 0.d0)
        do i = 1, sp_ob%aqu
          iob = sp_ob1%aqu + i - 1
          val(i) = aqu_d(ob(iob)%num)%dep_wt               !m
        end do

      case ("aquifer_water__volume_outflow_rate")
        allocate (val(sp_ob%aqu), source = 0.d0)
        do i = 1, sp_ob%aqu
          iob = sp_ob1%aqu + i - 1
          val(i) = ob(iob)%hd(1)%flo / 86400.d0            !m3 -> m3/s
        end do

      case ("soil_water_table__recharge_rate")
        allocate (val(sp_ob%aqu), source = 0.d0)
        do i = 1, sp_ob%aqu
          iob = sp_ob1%aqu + i - 1
          val(i) = aqu_d(ob(iob)%num)%rchrg / 86400000.d0   !mm/day -> m/s
        end do

      case ("point_source_water__volume_flow_rate")
        allocate (val(sp_ob%recall), source = 0.d0)
        do i = 1, sp_ob%recall
          iob = sp_ob1%recall + i - 1
          val(i) = ob(iob)%hd(1)%flo / 86400.d0            !m3 -> m3/s
        end do

      case ("place_of_use_water__volume_demand_rate")
        allocate (val(db_mx%wallo_pou), source = 0.d0)
        do i = 1, db_mx%wallo_pou
          val(i) = pou_dmd_day(i) / 86400.d0               !m3 -> m3/s
        end do

      case ("place_of_use_water__volume_withdrawal_rate")
        allocate (val(db_mx%wallo_pou), source = 0.d0)
        do i = 1, db_mx%wallo_pou
          val(i) = pou_wdr_day(i) / 86400.d0               !m3 -> m3/s
        end do

      case ("reservoir_water__volume")
        !! at the end of the day just done
        allocate (val(sp_ob%res), source = 0.d0)
        do i = 1, sp_ob%res
          val(i) = res(ob(sp_ob1%res + i - 1)%num)%flo                !m3
        end do

      case ("reservoir_water_surface__area")
        !! on SWAT+'s volume-area curve at the end of the day just done
        allocate (val(sp_ob%res), source = 0.d0)
        do i = 1, sp_ob%res
          val(i) = res_wat_d(ob(sp_ob1%res + i - 1)%num)%area_ha * 10000.d0   !ha -> m2
        end do

      case ("reservoir_water__volume_inflow_rate", "reservoir_water__volume_outflow_rate",       &
            "reservoir_water__precipitation_volume_flow_rate",                                    &
            "reservoir_water__evaporation_volume_flow_rate",                                      &
            "reservoir_water__seepage_volume_flow_rate",                                          &
            "reservoir_water__groundwater_volume_exchange_rate")
        !! the day just done, kept in exch_res; 0 on a day the reservoir wasn't built
        select case (name)
        case ("reservoir_water__groundwater_volume_exchange_rate"); k = 1
        case ("reservoir_water__volume_inflow_rate"); k = 2
        case ("reservoir_water__volume_outflow_rate"); k = 3
        case ("reservoir_water__precipitation_volume_flow_rate"); k = 4
        case ("reservoir_water__evaporation_volume_flow_rate"); k = 5
        case default; k = 6
        end select
        allocate (val(sp_ob%res), source = 0.d0)
        do i = 1, sp_ob%res
          val(i) = rsv_day(k,i) / 86400.d0                             !m3 -> m3/s
        end do

      case ("channel_water__irrigation_withdrawal_volume_flow_rate")
        !! what the day's irrigate actions and paddy irrigation took from the channel (exch_irr_take)
        allocate (val(sp_ob%chandeg), source = 0.d0)
        do i = 1, sp_ob%chandeg
          val(i) = irr_cha_day(i) / 86400.d0                !m3 -> m3/s
        end do

      case ("reservoir_water__irrigation_withdrawal_volume_flow_rate")
        !! what the day's irrigate actions and paddy irrigation took from the reservoir (exch_irr_take)
        allocate (val(sp_ob%res), source = 0.d0)
        do i = 1, sp_ob%res
          val(i) = irr_res_day(i) / 86400.d0                !m3 -> m3/s
        end do

      case ("land_irrigation_water__withdrawal_volume_flow_rate")
        !! what the HRU's irrigate actions and paddy irrigation took from SWAT+ channels and reservoirs the day
        !! just done (exch_irr_take), before the application's loss and runoff
        allocate (val(sp_ob%hru), source = 0.d0)
        do i = 1, sp_ob%hru
          val(i) = irr_hru_day(ob(sp_ob1%hru + i - 1)%num) / 86400.d0   !m3 -> m3/s
        end do

      case ("land_irrigation_water__volume_flow_rate")
        !! the day's irrigation on the HRU, from any source: what reached the soil and
        !! what ran off (irrig applied and runoff, mm), not what an application lost
        allocate (val(sp_ob%hru), source = 0.d0)
        do i = 1, sp_ob%hru
          iob = sp_ob1%hru + i - 1
          val(i) = hru_irr_day(ob(iob)%num) * hru(ob(iob)%num)%area_ha * 10.d0 / 86400.d0   !mm*ha -> m3/s
        end do

      case ("land_irrigation_water__infiltration_volume_flow_rate", "land_irrigation_water__runoff_volume_flow_rate", &
            "land_irrigation_water_no3__mass_flow_rate", "land_irrigation_water_solp__mass_flow_rate")
        !! the irrigation applied from outside on the day just done, as it went in (exch_hru_irr)
        k = ira_kind (name)
        allocate (val(sp_ob%hru), source = 0.d0)
        do i = 1, sp_ob%hru
          val(i) = ira_day(k, ob(sp_ob1%hru + i - 1)%num) / 86400.d0   !m3/day -> m3/s, kg/day -> kg/s
        end do

      case ("soil_layer__saturated_zone_flag")
        !! the HRU soil layers held below a water table (exch_hru_held), 1, the others 0
        allocate (val(n_hly), source = 0.d0)
        do i = 1, n_hly
          if (hly_held(i)) val(i) = 1.d0
        end do

      case ("land_surface_water__saturation_excess_runoff_rate")
        !! the water swr_satexcess sent out of the top of the profile the day just done
        !! (exch_hru_satex), part of the surface runoff (or of a ponded HRU's water)
        allocate (val(sp_ob%hru), source = 0.d0)
        do i = 1, sp_ob%hru
          val(i) = sat_day(ob(sp_ob1%hru + i - 1)%num) / 86400000.d0   !mm/day -> m/s
        end do

      case ("soil_water__groundwater_inflow_not_taken_rate")
        !! the groundwater set into the HRU's soil the day just done that didn't fit: every layer
        !! held and full (exch_hru_gwsoil); set less this is what went in
        allocate (val(sp_ob%hru), source = 0.d0)
        do i = 1, sp_ob%hru
          val(i) = nt_day(ob(sp_ob1%hru + i - 1)%num) / 86400000.d0    !mm/day -> m/s
        end do

      case ("soil_surface_cover__mass-per-area_density")
        !! the soil cover the HRU's soil evaporation used the day just done, set or SWAT+'s own
        !! (exch_hru_cover); NaN on a day et_act computed none
        allocate (val(sp_ob%hru), source = 0.d0)
        do i = 1, sp_ob%hru
          val(i) = cov_day(ob(sp_ob1%hru + i - 1)%num) / 10000.d0     !kg/ha -> kg/m2
        end do

      case ("land_vegetation__above-ground_mass-per-area_density", "land_vegetation_leaf__mass-per-area_density", &
            "land_surface_residue__mass-per-area_density")
        !! the plant community's above-ground and leaf mass as SWAT+ last summed them
        !! (pl_community, before the day's growth, less what senescence and dormancy dropped
        !! since), and the surface residue, at the end of the day just done: the next day's
        !! et_act takes the above-ground mass plus the residue as its soil cover
        allocate (val(sp_ob%hru), source = 0.d0)
        do i = 1, sp_ob%hru
          k = ob(sp_ob1%hru + i - 1)%num
          select case (name)
          case ("land_vegetation__above-ground_mass-per-area_density"); val(i) = pl_mass(k)%ab_gr_com%m
          case ("land_vegetation_leaf__mass-per-area_density");          val(i) = pl_mass(k)%leaf_com%m
          case default;                                                  val(i) = pl_mass(k)%rsd_tot%m
          end select
          val(i) = val(i) / 10000.d0                                   !kg/ha -> kg/m2
        end do

      case default
        iv = var_index(name)
        if (iv == 0) then
          call reply ("@error unknown variable: " // name)
          values_of = .false.
          return
        end if
        if (wx_kind(name) > 0) then
          call wx_values (wx_kind(name), val)
        else if (var_fam(iv) == gen_fam) then
          call gen_values (var_sp(iv), var_grid(iv), val)
          !! no record before the first day or after a day in print.prt's skipped years
          if (.not. out_made()) val = ieee_value (0.d0, ieee_quiet_nan)
        else
          call chem_values (var_fam(iv), var_sp(iv), val)
        end if
      end select
      values_of = .true.

      return
      end function values_of

!!    --------------------------------------------------------------------
      subroutine chem_values (fam, k, val)

!!    the day just done, for species k: a channel's outflow concentration
!!    (mass over volume of the day's outflow, 0 with no outflow), the mass
!!    exchanged with groundwater, an aquifer's recharge mass (what SWAT+ takes
!!    into the aquifer, aqu_1d_control) and a point source's load (0 for a
!!    diversion, which takes the channel's own concentration)

      use hydrograph_module, only : sp_ob, sp_ob1, ob
      use constituent_mass_module, only : obcs, obcs_alloc

      integer, intent(in) :: fam
      integer, intent(in) :: k
      real(8), dimension(:), allocatable, intent(out) :: val
      integer :: i = 0
      integer :: iob = 0

      select case (fam)

      case (fam_conc)
        allocate (val(sp_ob%chandeg), source = 0.d0)
        do i = 1, sp_ob%chandeg
          iob = sp_ob1%chandeg + i - 1
          if (ob(iob)%hd(1)%flo > 0.) val(i) = mass_of (iob, k, .false.) / ob(iob)%hd(1)%flo   !kg/m3
        end do

      case (fam_exch)
        allocate (val(sp_ob%chandeg), source = 0.d0)
        do i = 1, sp_ob%chandeg
          val(i) = chm_day(k,i) / 86400.d0                !kg/day -> kg/s
        end do

      case (fam_rchg)
        allocate (val(sp_ob%aqu), source = 0.d0)
        do i = 1, sp_ob%aqu
          iob = sp_ob1%aqu + i - 1
          val(i) = mass_of (iob, k, .true.) / 86400.d0    !kg/day -> kg/s
        end do

      case (fam_load)
        allocate (val(sp_ob%recall), source = 0.d0)
        do i = 1, sp_ob%recall
          iob = sp_ob1%recall + i - 1
          val(i) = mass_of (iob, k, .false.) / 86400.d0   !kg/day -> kg/s
        end do

      end select

      return

      contains

      real(8) function mass_of (iob, k, inflow)
        !! kg of species k in an object's outflow (hd(1)) or inflow (hin) for the day
        integer, intent(in) :: iob
        integer, intent(in) :: k
        logical, intent(in) :: inflow
        mass_of = 0.d0
        if (k <= n_salt) then
          if (obcs_alloc(iob) /= 1) return
          if (inflow) then
            mass_of = obcs(iob)%hin(1)%salt(k)
          else
            mass_of = obcs(iob)%hd(1)%salt(k)
          end if
        else if (k == n_salt + 1) then
          if (inflow) then
            mass_of = ob(iob)%hin%no3
          else
            mass_of = ob(iob)%hd(1)%no3
          end if
        else
          if (inflow) then
            mass_of = ob(iob)%hin%solp
          else
            mass_of = ob(iob)%hd(1)%solp
          end if
        end if
      end function mass_of

      end subroutine chem_values

!!    --------------------------------------------------------------------
      logical function out_made ()

!!    whether SWAT+ made its daily output records for the day just done:
!!    command makes them only in the years it summarizes, after print.prt's
!!    nyskip. At a pause the day about to run is time%day of year time%yrs,
!!    so on a year's first day the day just done was in the year before

      use time_module, only : time
      use basin_module, only : pco

      integer :: yr = 0

      out_made = .false.
      if (days_done == 0) return
      yr = time%yrs
      if (.not. ended .and. time%yrs > 1 .and. time%day == time%day_start) yr = time%yrs - 1
      out_made = yr > pco%nyskip

      return
      end function out_made

!!    --------------------------------------------------------------------
      subroutine gen_values (ig, grid, val)

!!    output column ig for every object on its grid: an HRU, channel or
!!    aquifer by its object's number, a landscape unit by its number, and
!!    the basin's one record (none without landscape unit elements, as
!!    SWAT+ then makes no basin records)

      use hydrograph_module, only : sp_ob, sp_ob1, ob
      use maximum_data_module, only : db_mx

      integer, intent(in) :: ig
      character(len=*), intent(in) :: grid
      real(8), dimension(:), allocatable, intent(out) :: val
      integer :: i = 0
      integer :: first = 0
      integer :: n = 0

      select case (trim(grid))
      case ("hru")
        first = sp_ob1%hru; n = sp_ob%hru
      case ("cha")
        first = sp_ob1%chandeg; n = sp_ob%chandeg
      case ("aqu")
        first = sp_ob1%aqu; n = sp_ob%aqu
      case ("lsu")
        first = 0; n = db_mx%lsu_out
      case default                                        !the basin
        first = 0; n = min (1, db_mx%lsu_elem)
      end select
      allocate (val(n), source = 0.d0)
      do i = 1, n
        if (first > 0) then
          val(i) = gen_value (ig, ob(first + i - 1)%num)
        else
          val(i) = gen_value (ig, i)
        end if
      end do

      return
      end subroutine gen_values

!!    --------------------------------------------------------------------
      integer function var_index (name)

!!    the catalog position of a variable, 0 if it isn't one

      character(len=*), intent(in) :: name
      integer :: iv = 0

      var_index = 0
      do iv = 1, nvar
        if (trim(var_name(iv)) == name) then
          var_index = iv
          return
        end if
      end do

      return
      end function var_index

!!    --------------------------------------------------------------------
      subroutine take_value (name, i, v, keep, msg)

!!    checks a value for the next day, and keeps it if keep is true; msg says
!!    why it is refused, blank if it isn't. Refused when SWAT+ would not use
!!    it as given: a point source that isn't daily or has no record in the
!!    next day's year, a groundwater exchange with sub-daily routing or on a
!!    channel of no length, a mass with a loss, a load with a diversion, a
!!    value that isn't a finite number

      use time_module
      use hydrograph_module, only : sp_ob, sp_ob1, ob, recall
      use maximum_data_module, only : db_mx
      use basin_module, only : bsn_cc

      character(len=*), intent(in) :: name
      integer, intent(in) :: i
      real(8), intent(in) :: v                            !m3/s, kg/s, deg C or kg/m2
      logical, intent(in) :: keep
      character(len=*), intent(out) :: msg
      integer :: irec = 0
      integer :: iv = 0
      integer :: yr = 0
      integer :: iday = 0

      msg = ""

      if (.not. ieee_is_finite(v)) then
        msg = "a value must be a finite number"
        return
      end if
      if (abs(v) * 86400.d0 > huge(1.)) then
        msg = "the value is too large for SWAT+'s 4-byte reals"
        return
      end if

      select case (name)

      case ("point_source_water__volume_flow_rate")
        call check_recall (i, msg)
        if (msg /= "") return
        irec = ob(sp_ob1%recall + i - 1)%num
        if (v < 0.d0 .and. any(rcm_on(:,i))) then
          msg = "a load is set for point source " // trim(recall(irec)%name) // " on that day; " //  &
                "a diversion takes the channel's own concentration, so it can't carry one"
          return
        end if
        if (keep) then
          rec_flo(i) = real(v * 86400.d0)                   !m3/s -> m3/day
          rec_flo_on(i) = .true.
        end if

      case ("channel_water__groundwater_volume_exchange_rate")
        call check_exchange (i, msg)
        if (msg /= "") return
        if (v <= 0.d0 .and. any(chm_on(:,i))) then
          msg = "a mass is set with channel " // trim(ob(sp_ob1%chandeg + i - 1)%name) //       &
                "'s gain on that day; a loss takes the outflow's own concentration, so it can't carry one"
          return
        end if
        if (keep) then
          chx_flo(i) = real(v * 86400.d0)                   !m3/s -> m3/day
          chx_on(i) = .true.
        end if

      case ("reservoir_water__groundwater_volume_exchange_rate")
        if (i < 1 .or. i > sp_ob%res) then
          msg = "index out of range"
          return
        end if
        if (time%step > 1) then
          msg = "the groundwater exchange works with daily routing only; this "  //  &
                "project routes " // itoa(time%step) // " steps a day (time.sim)"
          return
        end if
        if (bsn_cc%gwflow == 1) then
          msg = "gwflow computes reservoir " // trim(ob(sp_ob1%res + i - 1)%name) // "'s seepage " //  &
                "itself (gwflow_reservoir); a groundwater exchange set as well would count it twice"
          return
        end if
        if (keep) then
          rsx_flo(i) = real(v * 86400.d0)                   !m3/s -> m3/day
          rsx_on(i) = .true.
        end if

      case ("place_of_use_water__volume_demand_rate")
        if (i < 1 .or. i > db_mx%wallo_pou) then
          msg = "index out of range"
          return
        end if
        if (v < 0.d0) then
          msg = "a demand can't be negative"
          return
        end if
        if (keep) then
          dmd_flo(i) = real(v * 86400.d0)                   !m3/s -> m3/day
          dmd_flo_on(i) = .true.
        end if

      case ("soil_layer__temperature")
        if (i < 1 .or. i > n_hly) then
          msg = "index out of range"
          return
        end if
        if (v < -273.15d0) then
          msg = "a temperature can't be below absolute zero"
          return
        end if
        if (keep) then
          hly_tmp(i) = real(v)                            !deg C
          hly_on(i) = .true.
        end if

      case ("soil_layer_water__groundwater_inflow_rate")
        if (i < 1 .or. i > n_hly) then
          msg = "index out of range"
          return
        end if
        if (v < 0.d0) then
          msg = "groundwater into the soil can't be negative; water leaving the soil profile " //  &
                "is percolation (soil_water__percolation_rate)"
          return
        end if
        if (abs(v) * 86400000.d0 > huge(1.)) then
          msg = "the value is too large for SWAT+'s 4-byte reals"
          return
        end if
        if (bsn_cc%gwflow == 1) then
          msg = "the project runs SWAT+'s gwflow, which puts its own groundwater into the soil"
          return
        end if
        if (keep) then
          hly_gw(i) = real(v * 86400000.d0)               !m/s -> mm/day
          hly_gw_on(i) = .true.
        end if

      case ("soil_layer_water_no3__groundwater_inflow_mass_rate", "soil_layer_water_solp__groundwater_inflow_mass_rate")
        if (i < 1 .or. i > n_hly) then
          msg = "index out of range"
          return
        end if
        if (v < 0.d0) then
          msg = "a mass with groundwater into the soil can't be negative"
          return
        end if
        if (.not. hly_gw_on(i) .or. (v > 0.d0 .and. hly_gw(i) <= 0.)) then
          msg = "no groundwater is set into that layer for the next day; set " //                   &
                "soil_layer_water__groundwater_inflow_rate first: the mass comes with the water"
          return
        end if
        if (keep) then
          if (name(18:21) == "solp") then
            hly_gwm(2,i) = real(v * 86400.d0)             !kg/s -> kg/day
            hly_gwm_on(2,i) = .true.
          else
            hly_gwm(1,i) = real(v * 86400.d0)
            hly_gwm_on(1,i) = .true.
          end if
        end if

      case ("land_irrigation_water__infiltration_volume_flow_rate", "land_irrigation_water__runoff_volume_flow_rate", &
            "land_irrigation_water_no3__mass_flow_rate", "land_irrigation_water_solp__mass_flow_rate")
        call take_irrigation (ira_kind(name), i, v, keep, msg)

      case ("soil_layer__saturated_zone_flag")
        if (i < 1 .or. i > n_hly) then
          msg = "index out of range"
          return
        end if
        if (v /= 0.d0 .and. v /= 1.d0) then
          msg = "a saturated zone flag is 0 (not held) or 1 (held below the water table)"
          return
        end if
        if (bsn_cc%gwflow == 1) then
          msg = "the project runs SWAT+'s gwflow, which keeps its own water table in the soil"
          return
        end if
        if (keep) hly_held(i) = (v == 1.d0)

      case ("soil_surface_cover__mass-per-area_density")
        if (i < 1 .or. i > sp_ob%hru) then
          msg = "index out of range"
          return
        end if
        if (v < 0.d0) then
          msg = "a soil cover can't be negative"
          return
        end if
        if (v * 10000.d0 > huge(1.)) then
          msg = "the value is too large for SWAT+'s 4-byte reals"
          return
        end if
        if (keep) then
          cov_set(ob(sp_ob1%hru + i - 1)%num) = real(v * 10000.d0)      !kg/m2 -> kg/ha
          cov_on(ob(sp_ob1%hru + i - 1)%num) = .true.
        end if

      case default
        iv = var_index(name)
        if (iv == 0) then
          msg = "not a settable variable: " // name
        else if (var_role(iv) /= "in") then
          msg = "not a settable variable: " // name
        else if (wx_kind(name) > 0) then
          !! a weather record for the first day SWAT+ hasn't read
          yr = time%yrc
          iday = time%day
          call add_days (yr, iday, wx_lead(wx_kind(name)))
          call take_weather (wx_kind(name), i, yr, iday, v, keep, msg)
        else
          call take_chem (var_fam(iv), var_sp(iv), i, v, keep, msg)
        end if
        return
      end select

      return
      end subroutine take_value

!!    --------------------------------------------------------------------
      subroutine take_chem (fam, k, i, v, keep, msg)

!!    take_value for a mass of species k: one set with a channel's gain for
!!    the next day, which needs the gain set first, or a point source's load,
!!    which SWAT+ reads from the same daily records as its flow

      use time_module
      use hydrograph_module, only : sp_ob1, ob, recall
      use constituent_mass_module, only : rec_salt

      integer, intent(in) :: fam
      integer, intent(in) :: k
      integer, intent(in) :: i
      real(8), intent(in) :: v                            !kg/s
      logical, intent(in) :: keep
      character(len=*), intent(out) :: msg
      integer :: irec = 0
      real :: flo = 0.

      msg = ""
      if (v < 0.d0) then
        msg = "a mass can't be negative"
        return
      end if

      select case (fam)

      case (fam_exch)
        call check_exchange (i, msg)
        if (msg /= "") return
        if (.not. (chx_on(i) .and. chx_flo(i) > 0.)) then
          msg = "set channel " // trim(ob(sp_ob1%chandeg + i - 1)%name) // "'s groundwater gain " //  &
                "for the day first: a mass goes in with a gain, and a loss takes the outflow's own concentration"
          return
        end if
        if (keep) then
          chm_flo(k,i) = real(v * 86400.d0)               !kg/s -> kg/day
          chm_on(k,i) = .true.
        end if

      case (fam_load)
        call check_recall (i, msg)
        if (msg /= "") return
        irec = ob(sp_ob1%recall + i - 1)%num
        if (k <= n_salt) then
          if (.not. allocated(rec_salt)) then
            msg = "SWAT+ has read no point source salt records (salt_recall.rec; 62.0.1 never " //   &
                  "calls recall_read_salt), so a salt load would not be used"
            return
          end if
          if (irec > ubound(rec_salt,1)) then
            msg = "point source " // trim(recall(irec)%name) // " has no salt record in salt_recall.rec"
            return
          end if
          if (rec_salt(irec)%typ /= 1 .or. .not. allocated(rec_salt(irec)%hd_salt)) then
            msg = "point source " // trim(recall(irec)%name) // "'s salt record is type " //            &
                  itoa(rec_salt(irec)%typ) // " in salt_recall.rec; only daily salt records (type 1) can be set"
            return
          end if
          if (time%yrs > ubound(rec_salt(irec)%hd_salt,2)) then
            msg = "point source " // trim(recall(irec)%name) // "'s salt file has no year " //           &
                  itoa(time%yrs) // " of the simulation"
            return
          end if
        end if
        flo = recall(irec)%hd(time%day,time%yrs)%flo
        if (rec_flo_on(i)) flo = rec_flo(i)
        if (flo < 0.) then
          msg = "point source " // trim(recall(irec)%name) // " is a diversion on that day; it " //     &
                "takes the channel's own concentration, so a load can't be set"
          return
        end if
        if (keep) then
          rcm_flo(k,i) = real(v * 86400.d0)               !kg/s -> kg/day
          rcm_on(k,i) = .true.
        end if

      end select

      return
      end subroutine take_chem

!!    --------------------------------------------------------------------
      integer function ira_kind (name)

!!    which of the irrigation applied from outside a variable is (ira_set)

      character(len=*), intent(in) :: name

      select case (name)
      case ("land_irrigation_water__infiltration_volume_flow_rate"); ira_kind = 1
      case ("land_irrigation_water__runoff_volume_flow_rate");       ira_kind = 2
      case ("land_irrigation_water_no3__mass_flow_rate");            ira_kind = 3
      case default;                                                  ira_kind = 4
      end select

      return
      end function ira_kind

!!    --------------------------------------------------------------------
      subroutine take_irrigation (k, i, v, keep, msg)

!!    take_value for irrigation applied to HRU i from outside the next day
!!    (exch_hru_irr): k 1 water into the soil, 2 water that runs off (m3/s),
!!    3 nitrate, 4 soluble P (kg/s). Refused: a negative value, an HRU of no
!!    area, a wetland or paddy HRU (SWAT+ puts irrigation there into the
!!    ponded water, and its own paddy irrigation replaces the day's), and a
!!    nitrate or P mass with no water set into the soil for the HRU that day

      use hydrograph_module, only : sp_ob, sp_ob1, ob
      use hru_module, only : hru

      integer, intent(in) :: k
      integer, intent(in) :: i
      real(8), intent(in) :: v                            !m3/s or kg/s
      logical, intent(in) :: keep
      character(len=*), intent(out) :: msg
      integer :: j = 0

      msg = ""
      if (i < 1 .or. i > sp_ob%hru) then
        msg = "index out of range"
        return
      end if
      j = ob(sp_ob1%hru + i - 1)%num
      if (v < 0.d0) then
        msg = "irrigation applied to an HRU can't be negative"
        return
      end if
      if (v > 0.d0) then
        if (hru(j)%area_ha <= 0.) then
          msg = "HRU " // trim(ob(sp_ob1%hru + i - 1)%name) // " has no area"
          return
        end if
        if (v * 86400.d0 / hru(j)%area_ha > huge(1.)) then
          msg = "the value is too large for SWAT+'s 4-byte reals"
          return
        end if
        if (hru(j)%dbs%surf_stor > 0) then
          msg = "HRU " // trim(ob(sp_ob1%hru + i - 1)%name) // " is a wetland or paddy (surf_stor in " //   &
                "hru-data.hru): SWAT+ puts its irrigation into the ponded water, and its paddy irrigation " //  &
                "replaces the day's, so irrigation set from outside would not be used as given"
          return
        end if
        if (k > 2 .and. .not. (ira_on(1,j) .and. ira_set(1,j) > 0.d0)) then
          msg = "no irrigation water is set into HRU " // trim(ob(sp_ob1%hru + i - 1)%name) // "'s soil " //  &
                "for the next day; set land_irrigation_water__infiltration_volume_flow_rate first: " //       &
                "the nitrate and phosphorus come with the water"
          return
        end if
      end if
      if (k == 1 .and. v <= 0.d0 .and. any(ira_on(3:4,j) .and. ira_set(3:4,j) > 0.d0)) then
        msg = "nitrate or phosphorus is set with HRU " // trim(ob(sp_ob1%hru + i - 1)%name) // "'s " //    &
              "irrigation water for the next day, so the water into the soil can't be set to 0"
        return
      end if
      if (keep) then
        ira_set(k,j) = v
        ira_on(k,j) = .true.
      end if

      return
      end subroutine take_irrigation

!!    --------------------------------------------------------------------
      subroutine check_recall (i, msg)

!!    a point source SWAT+ reads a daily record for on the next day

      use time_module
      use hydrograph_module, only : sp_ob, sp_ob1, ob, recall

      integer, intent(in) :: i
      character(len=*), intent(out) :: msg
      integer :: irec = 0

      msg = ""
      if (i < 1 .or. i > sp_ob%recall) then
        msg = "index out of range"
        return
      end if
      irec = ob(sp_ob1%recall + i - 1)%num
      if (recall(irec)%typ /= 1) then
        !! sub-daily, monthly, yearly and constant records are kept in other shapes
        msg = "point source " // trim(recall(irec)%name) // " is recall type " //  &
              itoa(recall(irec)%typ) // "; only daily point sources (type 1) can be set"
        return
      end if
      if (time%yrc < recall(irec)%start_yr .or. time%yrc > recall(irec)%end_yr) then
        !! SWAT+ gives a daily point source no flow outside its record
        msg = "point source " // trim(recall(irec)%name) // " has no record in " //   &
              itoa(time%yrc) // " (its file covers " // itoa(recall(irec)%start_yr) //    &
              "-" // itoa(recall(irec)%end_yr) // "), so SWAT+ would not use the value"
        return
      end if

      return
      end subroutine check_recall

!!    --------------------------------------------------------------------
      subroutine check_exchange (i, msg)

!!    a channel SWAT+ routes a groundwater exchange through on the next day:
!!    daily routing, and a channel with length (one of no length passes its
!!    inflow straight on in command and never reaches sd_channel_control3)

      use time_module
      use hydrograph_module, only : sp_ob, sp_ob1, ob
      use sd_channel_module, only : sd_ch

      integer, intent(in) :: i
      character(len=*), intent(out) :: msg
      integer :: iob = 0

      msg = ""
      if (i < 1 .or. i > sp_ob%chandeg) then
        msg = "index out of range"
        return
      end if
      if (time%step > 1) then
        msg = "the groundwater exchange works with daily routing only; this "  //  &
              "project routes " // itoa(time%step) // " steps a day (time.sim)"
        return
      end if
      iob = sp_ob1%chandeg + i - 1
      if (sd_ch(ob(iob)%num)%chl <= 1.e-3) then
        msg = "channel " // trim(ob(iob)%name) // " has no length (chl), so SWAT+ passes its " //   &
              "inflow straight on and routes no exchange through it"
        return
      end if

      return
      end subroutine check_exchange

!!    --------------------------------------------------------------------
      subroutine set_value (name, i, v)

!!    set <var> <i> <v>: one value for the next day

      character(len=*), intent(in) :: name
      integer, intent(in) :: i
      real(8), intent(in) :: v
      character(len=400) :: msg = ""

      if (name == "soil_layer__saturated_zone_flag") then
        call take_value (name, i, v, .false., msg)
        if (msg == "") msg = held_order([i], [v])
        if (msg /= "") then
          call reply ("@error " // trim(msg))
          return
        end if
      end if
      call take_value (name, i, v, .true., msg)
      if (msg /= "") then
        call reply ("@error " // trim(msg))
      else
        call reply ("@ok")
      end if

      return
      end subroutine set_value

!!    --------------------------------------------------------------------
      subroutine set_values (name, n, indexed)

!!    setv <var> <n>: every object on the variable's grid, in grid order;
!!    seti <var> <n>: the objects given. After @send come the n values as
!!    8-byte reals, for seti after n 4-byte 1-based indices. All or nothing:
!!    if any value is refused, none is kept, and the reply names the first
!!    one refused.

      character(len=*), intent(in) :: name
      integer, intent(in) :: n
      logical, intent(in) :: indexed
      character(len=400) :: msg = ""
      integer(c_int32_t), dimension(:), allocatable :: idx
      real(8), dimension(:), allocatable :: val
      integer :: k = 0
      integer :: bad = 0

      if (.not. indexed .and. n /= var_size(name)) then
        call reply ("@error setv needs all " // itoa(var_size(name)) // " values of " // name)
        return
      end if
      allocate (idx(n), source = 0_c_int32_t)
      allocate (val(n), source = 0.d0)
      call reply ("@send")
      if (indexed) then
        call get_bytes (idx, 4 * size(idx, kind=c_size_t))
      else
        idx = [(int(k, c_int32_t), k = 1, n)]
      end if
      call get_bytes (val, 8 * size(val, kind=c_size_t))
      bad = 0
      do k = 1, n
        if (bad /= 0) exit
        call take_value (name, int(idx(k)), val(k), .false., msg)
        if (msg /= "") bad = k
      end do
      if (bad /= 0) then
        call reply ("@error value " // itoa(bad) // ": " // trim(msg))
        return
      end if
      if (name == "soil_layer__saturated_zone_flag") then
        msg = held_order(int(idx), val)
        if (msg /= "") then
          call reply ("@error " // trim(msg))
          return
        end if
      end if
      do k = 1, n
        call take_value (name, int(idx(k)), val(k), .true., msg)
      end do
      call reply ("@ok")

      return
      end subroutine set_values

!!    --------------------------------------------------------------------
      function held_order (idx, val) result (msg)

!!    the saturated zone flags as they would be with the values given (hly
!!    grid indices, 0 or 1): blank if every HRU's held layers are its bottom
!!    ones, else why not. A water table holds the layers below it, so a held
!!    layer can't be above one that isn't

      integer, dimension(:), intent(in) :: idx
      real(8), dimension(:), intent(in) :: val
      character(len=400) :: msg
      logical, dimension(:), allocatable :: trial
      integer :: k = 0

      msg = ""
      allocate (trial(n_hly), source = hly_held)
      do k = 1, size(idx)
        trial(idx(k)) = (val(k) == 1.d0)
      end do
      do k = 1, n_hly - 1
        if (hly_hru(k + 1) /= hly_hru(k)) cycle
        if (trial(k) .and. .not. trial(k + 1)) then
          msg = "HRU " // itoa(hly_hru(k)) // ": layer " // itoa(hly_lay(k)) // " would be held " //   &
                "and layer " // itoa(hly_lay(k + 1)) // " below it not; a water table holds every " // &
                "layer below it"
          return
        end if
      end do

      return
      end function held_order

!!    --------------------------------------------------------------------
      integer function var_size (name)

!!    the number of objects on a variable's grid, 0 for an unknown variable

      use hydrograph_module, only : sp_ob
      use maximum_data_module, only : db_mx

      character(len=*), intent(in) :: name
      integer :: iv = 0

      var_size = 0
      do iv = 1, nvar
        if (trim(var_name(iv)) /= name) cycle
        select case (trim(var_grid(iv)))
        case ("cha"); var_size = sp_ob%chandeg
        case ("aqu"); var_size = sp_ob%aqu
        case ("rec"); var_size = sp_ob%recall
        case ("res"); var_size = sp_ob%res
        case ("pou"); var_size = db_mx%wallo_pou
        case ("hru"); var_size = sp_ob%hru
        case ("hly"); var_size = n_hly
        case ("pcp"); var_size = wx_count (1)
        case ("tmp"); var_size = wx_count (2)
        case ("slr"); var_size = wx_count (4)
        case ("hmd"); var_size = wx_count (5)
        case ("wnd"); var_size = wx_count (6)
        end select
      end do

      return
      end function var_size

!!    --------------------------------------------------------------------
      subroutine set_demand (ipou, dmd)

!!    replaces the demand wallo_start_day set for a place of use, and the
!!    duty of each of its points of diversion, the way wallo_start_day
!!    does: capped at the place of use's maximum rate and split by the
!!    points of diversion's fractions

      use water_allocation_module, only : pou, poud_met

      integer, intent(in) :: ipou
      real, intent(in) :: dmd                             !m3/day
      integer :: ipod = 0

      pou(ipou)%demand = Min(pou(ipou)%rate_max * 86400., dmd)
      poud_met(ipou)%duty_tot%duty = pou(ipou)%demand
      do ipod = 1, pou(ipou)%pods
        pou(ipou)%pod(ipod)%duty = pou(ipou)%pod(ipod)%frac * pou(ipou)%demand
        poud_met(ipou)%pod(ipod)%duty = pou(ipou)%pod(ipod)%duty
      end do

      return
      end subroutine set_demand

!!    --------------------------------------------------------------------
      subroutine send_grid (gname, ifirst, n)

      use hydrograph_module, only : ob

      character(len=*), intent(in) :: gname
      integer, intent(in) :: ifirst
      integer, intent(in) :: n
      character(len=200) :: line = ""
      integer :: i = 0
      integer :: iob = 0

      call reply ("@grid " // gname // " " // itoa(n))
      do i = 1, n
        iob = ifirst + i - 1
        write (line, '(a,1x,i0,1x,a,3(1x,es15.8e2))') gname, i, trim(ob(iob)%name),   &
                     ob(iob)%long, ob(iob)%lat, ob(iob)%area_ha
        call reply ("@obj " // trim(line))
      end do

      return
      end subroutine send_grid

!!    --------------------------------------------------------------------
      subroutine send_lsu_grid

!!    landscape units (lsunit_* outputs), named as in ls_unit.def, with no
!!    location (NaN) and their area

      use calibration_data_module, only : lsu_out
      use maximum_data_module, only : db_mx

      character(len=200) :: line = ""
      integer :: i = 0
      real :: nan = 0.

      nan = ieee_value (nan, ieee_quiet_nan)
      call reply ("@grid lsu " // itoa(db_mx%lsu_out))
      do i = 1, db_mx%lsu_out
        write (line, '(a,1x,i0,1x,a,3(1x,es15.8e2))') "lsu", i, trim(adjustl(lsu_out(i)%name)),   &
                     nan, nan, lsu_out(i)%area_ha
        call reply ("@obj " // trim(line))
      end do

      return
      end subroutine send_lsu_grid

!!    --------------------------------------------------------------------
      subroutine send_bsn_grid

!!    the basin (basin_* outputs): one object, named as in SWAT+'s
!!    object.cnt, with no location (NaN) and its total area; none in a
!!    project without landscape unit elements (ls_unit.ele), where SWAT+
!!    makes no basin records (command calls basin_output only with them)

      use basin_module, only : bsn
      use maximum_data_module, only : db_mx

      character(len=200) :: line = ""
      real :: nan = 0.

      nan = ieee_value (nan, ieee_quiet_nan)
      call reply ("@grid bsn " // itoa(min (1, db_mx%lsu_elem)))
      if (db_mx%lsu_elem == 0) return
      write (line, '(a,1x,i0,1x,a,3(1x,es15.8e2))') "bsn", 1, trim(adjustl(bsn%name)),   &
                   nan, nan, bsn%area_tot_ha
      call reply ("@obj " // trim(line))

      return
      end subroutine send_bsn_grid

!!    --------------------------------------------------------------------
      subroutine send_hly_grid

!!    HRU soil layers: one point per layer, HRU by HRU, at the HRU's
!!    location and area, named <HRU>:<layer>, with a seventh field, the
!!    layer's mid-depth below the surface (m)

      use hydrograph_module, only : sp_ob1, ob
      use soil_module, only : soil

      character(len=240) :: line = ""
      integer :: i = 0
      integer :: iob = 0
      integer :: j = 0
      integer :: k = 0
      real :: top = 0.

      call reply ("@grid hly " // itoa(n_hly))
      iob = sp_ob1%hru - 1
      do i = 1, n_hly
        j = hly_hru(i)
        k = hly_lay(i)
        if (k == 1) iob = iob + 1                         !the next HRU, in grid order
        top = 0.
        if (k > 1) top = soil(j)%phys(k-1)%d
        write (line, '(a,1x,i0,1x,a,a,i0,4(1x,es15.8e2))') "hly", i, trim(ob(iob)%name), ":", k,  &
              ob(iob)%long, ob(iob)%lat, ob(iob)%area_ha, (top + soil(j)%phys(k)%d) / 2000.
        call reply ("@obj " // trim(line))
      end do

      return
      end subroutine send_hly_grid

!!    --------------------------------------------------------------------
      subroutine send_pou_grid

!!    water allocation places of use have no spatial object of their own;
!!    they go out with no location

      use water_allocation_module, only : pou
      use maximum_data_module, only : db_mx
      use, intrinsic :: ieee_arithmetic, only : ieee_value, ieee_quiet_nan

      character(len=200) :: line = ""
      integer :: i = 0
      real :: nan = 0.

      nan = ieee_value (nan, ieee_quiet_nan)
      call reply ("@grid pou " // itoa(db_mx%wallo_pou))
      do i = 1, db_mx%wallo_pou
        write (line, '(a,1x,i0,1x,a,3(1x,es15.8e2))') "pou", i, trim(pou(i)%name), nan, nan, nan
        call reply ("@obj " // trim(line))
      end do

      return
      end subroutine send_pou_grid

!!    --------------------------------------------------------------------
      subroutine send_array (val)

!!    the line @bin n, then the n values as 8-byte reals in this machine's
!!    byte order, so a value goes out exactly as SWAT+ holds it

      real(8), dimension(:), intent(in) :: val

      call reply ("@bin " // itoa(size(val)))
      call put_bytes (val, 8 * size(val, kind=c_size_t))

      return
      end subroutine send_array

!!    --------------------------------------------------------------------
      subroutine put_bytes (buf, nbytes)

!!    writes nbytes of buf to the reply channel, after the line before it

      type(*), dimension(*), intent(in) :: buf
      integer(c_size_t), intent(in) :: nbytes

      if (nbytes == 0) return
      if (swx_write (bin_out, buf, nbytes) /= 0) error stop 3

      return
      end subroutine put_bytes

!!    --------------------------------------------------------------------
      subroutine get_bytes (buf, nbytes)

!!    reads nbytes from the command channel into buf, the bytes the caller
!!    sends after @send. If the channel closes first, the caller is gone

      type(*), dimension(*) :: buf
      integer(c_size_t), intent(in) :: nbytes

      if (nbytes == 0) return
      if (swx_read (bin_in, buf, nbytes) /= 0) error stop 3

      return
      end subroutine get_bytes

!!    --------------------------------------------------------------------
      subroutine reply (line)

      character(len=*), intent(in) :: line

      !! one write of the line and its newline: the caller is gone if it fails
      if (swx_write_line (bin_out, line, len_trim(line, kind=c_size_t)) /= 0) error stop 3

      return
      end subroutine reply

!!    --------------------------------------------------------------------
      function slashed (path) result (s)

!!    path with each \ made /, so a Windows path compares with either

      character(len=*), intent(in) :: path
      character(len=len(path)) :: s
      integer :: i

      s = path
      do i = 1, len(s)
        if (s(i:i) == "\") s(i:i) = "/"
      end do

      return
      end function slashed

!!    --------------------------------------------------------------------
      function itoa (i) result (s)

      integer, intent(in) :: i
      character(len=:), allocatable :: s
      character(len=24) :: buf

      write (buf, '(i0)') i
      s = trim(buf)

      end function itoa

!!    --------------------------------------------------------------------
!!    weather records. A set writes into a gage's record (pcp, tmp, slr,
!!    hmd, wnd), as if its file had held that value, for a day SWAT+ hasn't
!!    read yet. The day's weather is read before the pause (climate_control),
!!    and precipitation a day ahead (precip_next, which decision tables use),
!!    so set_value writes the next day's record, or for precipitation the
!!    day after; setr writes any later day. SWAT+ then builds everything from
!!    the record itself: the mean temperature, PET, the generator's wet and
!!    dry days, heat units and the air temperature lag.

      integer function wx_kind (name)

!!    1 precipitation, 2 maximum and 3 minimum temperature, 4 solar
!!    radiation, 5 relative humidity, 6 wind speed; 0 for any other name

      character(len=*), intent(in) :: name

      select case (name)
      case ("atmosphere_water__precipitation_leq-volume_flux"); wx_kind = 1
      case ("atmosphere_bottom_air__max_of_temperature"); wx_kind = 2
      case ("atmosphere_bottom_air__min_of_temperature"); wx_kind = 3
      case ("land_surface_radiation~incoming~shortwave__energy_flux"); wx_kind = 4
      case ("atmosphere_bottom_air_water~vapor__relative_saturation"); wx_kind = 5
      case ("atmosphere_bottom_air_flow__speed"); wx_kind = 6
      case default; wx_kind = 0
      end select

      return
      end function wx_kind

!!    --------------------------------------------------------------------
      integer function wx_lead (kind)

!!    days from the coming day to the first record SWAT+ hasn't read

      integer, intent(in) :: kind

      wx_lead = 1
      if (kind == 1) wx_lead = 2

      return
      end function wx_lead

!!    --------------------------------------------------------------------
      integer function wx_count (kind)

      use maximum_data_module, only : db_mx

      integer, intent(in) :: kind

      select case (kind)
      case (1); wx_count = db_mx%pcpfiles
      case (2, 3); wx_count = db_mx%tmpfiles
      case (4); wx_count = db_mx%slrfiles
      case (5); wx_count = db_mx%rhfiles
      case (6); wx_count = db_mx%wndfiles
      case default; wx_count = 0
      end select

      return
      end function wx_count

!!    --------------------------------------------------------------------
      integer function ylen (yr)

!!    days in a year, as time_control counts them

      integer, intent(in) :: yr

      ylen = 365
      if (Mod(yr,4) == 0 .and. (Mod(yr,100) /= 0 .or. Mod(yr,400) == 0)) ylen = 366

      return
      end function ylen

!!    --------------------------------------------------------------------
      subroutine add_days (yr, iday, n)

!!    moves a date (year, day of the year) n days, back if n is negative

      integer, intent(inout) :: yr
      integer, intent(inout) :: iday
      integer, intent(in) :: n
      integer :: k = 0

      do k = 1, abs(n)
        if (n > 0) then
          iday = iday + 1
          if (iday > ylen(yr)) then
            yr = yr + 1
            iday = 1
          end if
        else
          iday = iday - 1
          if (iday < 1) then
            yr = yr - 1
            iday = ylen(yr)
          end if
        end if
      end do

      return
      end subroutine add_days

!!    --------------------------------------------------------------------
      logical function date_before (yr1, d1, yr2, d2)

      integer, intent(in) :: yr1, d1, yr2, d2

      date_before = yr1 < yr2 .or. (yr1 == yr2 .and. d1 < d2)

      return
      end function date_before

!!    --------------------------------------------------------------------
      subroutine wx_slot (kind, ig, yr, iday, icol, msg)

!!    the column of a gage's record table that holds a date, or why SWAT+
!!    wouldn't read a value there (the bounds as climate_control and
!!    cli_precip_control check them)

      use time_module
      use climate_module, only : pcp, tmp, slr, hmd, wnd, climate_measured_data

      integer, intent(in) :: kind
      integer, intent(in) :: ig
      integer, intent(in) :: yr
      integer, intent(in) :: iday
      integer, intent(out) :: icol
      character(len=*), intent(out) :: msg

      msg = ""
      icol = 0
      if (ig < 1 .or. ig > wx_count(kind)) then
        msg = "index out of range"
        return
      end if
      select case (kind)
      case (1); call slot (pcp(ig))
      case (2, 3); call slot (tmp(ig))
      case (4); call slot (slr(ig))
      case (5); call slot (hmd(ig))
      case (6); call slot (wnd(ig))
      end select

      return

      contains

      subroutine slot (g)
      type (climate_measured_data), intent(in) :: g
      integer :: yr_last = 0
      integer :: day_last = 0

      if (g%tstep > 0) then
        msg = "gage " // trim(g%filename) // " has sub-daily records; only daily ones can be set"
        return
      end if
      yr_last = time%yrc_start + time%nbyr - 1
      day_last = ylen(yr_last)
      if (time%day_end > 0) day_last = min(time%day_end, day_last)
      if (date_before(yr_last, day_last, yr, iday)) then
        msg = "day " // itoa(iday) // " of " // itoa(yr) // " is after the simulation ends"
        return
      end if
      icol = (yr - time%yrc_start + 1) - g%yrs_start
      if (date_before(yr, iday, g%start_yr, g%start_day) .or. date_before(g%end_yr, g%end_day, yr, iday)   &
          .or. icol < 1 .or. icol > g%nbyr                                                     &
          .or. (kind == 1 .and. icol > g%end_yr - g%start_yr + 1)) then
        msg = "gage " // trim(g%filename) // " has no record for day " // itoa(iday) // " of " //  &
              itoa(yr) // " (its file covers day " // itoa(g%start_day) // " of " //             &
              itoa(g%start_yr) // " to day " // itoa(g%end_day) // " of " // itoa(g%end_yr) //    &
              "), so SWAT+ would generate that day and not use the value"
        icol = 0
      end if

      return
      end subroutine slot

      end subroutine wx_slot

!!    --------------------------------------------------------------------
      subroutine wx_access (kind, ig, iday, icol, rec, put)

!!    reads (put false) or writes a gage's record for a day, in SWAT+'s units

      use climate_module, only : pcp, tmp, slr, hmd, wnd, climate_measured_data

      integer, intent(in) :: kind
      integer, intent(in) :: ig
      integer, intent(in) :: iday
      integer, intent(in) :: icol
      real, intent(inout) :: rec
      logical, intent(in) :: put

      select case (kind)
      case (1); call acc (pcp(ig))
      case (2, 3); call acc (tmp(ig))
      case (4); call acc (slr(ig))
      case (5); call acc (hmd(ig))
      case (6); call acc (wnd(ig))
      end select

      return

      contains

      subroutine acc (g)
      type (climate_measured_data), intent(inout) :: g

      if (kind == 3) then
        if (put) then
          g%ts2(iday,icol) = rec
        else
          rec = g%ts2(iday,icol)
        end if
      else
        if (put) then
          g%ts(iday,icol) = rec
        else
          rec = g%ts(iday,icol)
        end if
      end if

      return
      end subroutine acc

      end subroutine wx_access

!!    --------------------------------------------------------------------
      real(8) function wx_si (kind, rec)

!!    a record in SWAT+'s units to the variable's: mm/day to m/s, MJ/m2/day
!!    to W/m2; temperatures (deg C), humidity (a fraction) and wind (m/s) as
!!    they are

      integer, intent(in) :: kind
      real, intent(in) :: rec

      select case (kind)
      case (1); wx_si = dble(rec) / 86400000.d0
      case (4); wx_si = dble(rec) / 0.0864d0
      case default; wx_si = dble(rec)
      end select

      return
      end function wx_si

!!    --------------------------------------------------------------------
      subroutine take_weather (kind, ig, yr, iday, v, keep, msg)

!!    checks a weather value for a gage and a date, and writes it into the
!!    record if keep is true; msg says why it is refused, blank if it isn't

      use time_module

      integer, intent(in) :: kind
      integer, intent(in) :: ig
      integer, intent(in) :: yr
      integer, intent(in) :: iday
      real(8), intent(in) :: v
      logical, intent(in) :: keep
      character(len=*), intent(out) :: msg
      integer :: icol = 0
      integer :: yr1 = 0
      integer :: d1 = 0
      real :: rec = 0.

      msg = ""
      if (.not. ieee_is_finite(v)) then
        msg = "a value must be a finite number"
        return
      end if
      yr1 = time%yrc
      d1 = time%day
      call add_days (yr1, d1, wx_lead(kind))
      if (date_before(yr, iday, yr1, d1)) then
        msg = "SWAT+ has already read this record for day " // itoa(iday) // " of " // itoa(yr) //   &
              "; the first day that can be set now is day " // itoa(d1) // " of " // itoa(yr1)
        return
      end if
      call wx_slot (kind, ig, yr, iday, icol, msg)
      if (msg /= "") return

      select case (kind)
      case (1)
        if (v < 0.d0) msg = "precipitation can't be negative"
        rec = real(v * 86400000.d0)                       !m/s -> mm/day
      case (2, 3)
        if (v <= -97.d0) msg = "SWAT+ reads a temperature of -97 or below as missing"
        if (v > 100.d0) msg = "a temperature above 100 degC isn't weather"
        rec = real(v)                                     !deg C
      case (4)
        if (v < 0.d0) msg = "solar radiation can't be negative"
        rec = real(v * 0.0864d0)                          !W/m2 -> MJ/m2/day
      case (5)
        if (v < 0.d0 .or. v > 1.d0) msg = "relative humidity is a fraction, 0 to 1"
        rec = real(v)
      case (6)
        if (v < 0.d0) msg = "wind speed can't be negative"
        rec = real(v)                                     !m/s
      end select
      if (msg /= "") return

      if (keep) call wx_access (kind, ig, iday, icol, rec, .true.)

      return
      end subroutine take_weather

!!    --------------------------------------------------------------------
      subroutine wx_values (kind, val)

!!    each gage's record for the day just done; NaN where there's none (before
!!    the first day, outside the file's records, or missing in it)

      use time_module
      use, intrinsic :: ieee_arithmetic, only : ieee_value, ieee_quiet_nan

      integer, intent(in) :: kind
      real(8), dimension(:), allocatable, intent(out) :: val
      character(len=400) :: msg = ""
      integer :: ig = 0
      integer :: icol = 0
      integer :: yr = 0
      integer :: iday = 0
      real :: rec = 0.

      allocate (val(wx_count(kind)), source = ieee_value (0.d0, ieee_quiet_nan))
      if (days_done == 0) return
      if (days_done >= days_total) then
        !! the run is over: the last day
        yr = time%yrc_start + time%nbyr - 1
        iday = ylen(yr)
        if (time%day_end > 0) iday = min(time%day_end, iday)
      else
        yr = time%yrc
        iday = time%day
        call add_days (yr, iday, -1)
      end if
      do ig = 1, wx_count(kind)
        call wx_slot (kind, ig, yr, iday, icol, msg)
        if (msg /= "") cycle
        call wx_access (kind, ig, iday, icol, rec, .false.)
        if (rec > -97.) val(ig) = wx_si (kind, rec)
      end do

      return
      end subroutine wx_values

!!    --------------------------------------------------------------------
      subroutine set_records (name, yr, iday, nd, ngiven)

!!    setr <var> <year> <day> <nd> <ng>: the records for nd days from that
!!    date. After @send come nd x ng 8-byte reals, day by day, each day a
!!    value for every gage in grid order. All or nothing: if any value is
!!    refused, none is kept, and the reply names the first one refused.

      character(len=*), intent(in) :: name
      integer, intent(in) :: yr
      integer, intent(in) :: iday
      integer, intent(in) :: nd
      integer, intent(in) :: ngiven
      character(len=400) :: msg = ""
      real(8), dimension(:,:), allocatable :: val
      integer :: kind = 0
      integer :: ng = 0
      integer :: k = 0
      integer :: ig = 0
      integer :: y = 0
      integer :: d = 0
      integer :: bad = 0

      kind = wx_kind(name)
      if (kind == 0) then
        call reply ("@error not a weather record variable: " // name)
        return
      end if
      ng = max(wx_count(kind), 0)
      if (ngiven /= ng) then
        call reply ("@error setr needs a value for each of the " // itoa(ng) // " gages of " // name)
        return
      end if
      if (iday < 1 .or. iday > ylen(yr)) then
        call reply ("@error day " // itoa(iday) // " isn't a day of " // itoa(yr))
        return
      end if
      allocate (val(ng, nd), source = 0.d0)
      call reply ("@send")
      call get_bytes (val, 8 * size(val, kind=c_size_t))
      bad = 0
      msg = ""
      y = yr
      d = iday
      do k = 1, nd
        if (bad /= 0) exit
        do ig = 1, ng
          call take_weather (kind, ig, y, d, val(ig,k), .false., msg)
          if (msg /= "") then
            bad = k
            msg = "gage " // itoa(ig) // ": " // trim(msg)
            exit
          end if
        end do
        call add_days (y, d, 1)
      end do
      if (bad /= 0) then
        call reply ("@error day " // itoa(bad) // ": " // trim(msg))
        return
      end if
      y = yr
      d = iday
      do k = 1, nd
        do ig = 1, ng
          call take_weather (kind, ig, y, d, val(ig,k), .true., msg)
        end do
        call add_days (y, d, 1)
      end do
      call reply ("@ok")

      return
      end subroutine set_records

!!    --------------------------------------------------------------------
      subroutine send_wx_grid (gname, kind)

!!    a weather gage grid: the gages' file names, at their longitude and latitude

      use climate_module, only : pcp, tmp, slr, hmd, wnd
      use, intrinsic :: ieee_arithmetic, only : ieee_value, ieee_quiet_nan

      character(len=*), intent(in) :: gname
      integer, intent(in) :: kind
      character(len=200) :: line = ""
      character(len=50) :: fname = ""
      integer :: i = 0
      real :: lon = 0.
      real :: lat = 0.
      real :: nan = 0.

      nan = ieee_value (nan, ieee_quiet_nan)
      call reply ("@grid " // gname // " " // itoa(wx_count(kind)))
      do i = 1, wx_count(kind)
        select case (kind)
        case (1); fname = pcp(i)%filename; lon = pcp(i)%long; lat = pcp(i)%lat
        case (2); fname = tmp(i)%filename; lon = tmp(i)%long; lat = tmp(i)%lat
        case (4); fname = slr(i)%filename; lon = slr(i)%long; lat = slr(i)%lat
        case (5); fname = hmd(i)%filename; lon = hmd(i)%long; lat = hmd(i)%lat
        case (6); fname = wnd(i)%filename; lon = wnd(i)%long; lat = wnd(i)%lat
        end select
        write (line, '(a,1x,i0,1x,a,3(1x,es15.8e2))') gname, i, trim(fname), lon, lat, nan
        call reply ("@obj " // trim(line))
      end do

      return
      end subroutine send_wx_grid

      end module bmi_exchange_module
