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

      use iso_fortran_env, only : input_unit
      use, intrinsic :: iso_c_binding, only : c_int, c_char, c_null_char
      use, intrinsic :: ieee_arithmetic, only : ieee_is_finite

      implicit none

      private
      public :: exch_on, exch_open, exch_start, exch_pause, exch_apply, exch_record, exch_end,     &
                exch_channel, exch_hru_soil

      logical :: exch_on = .false.        !! exchange mode is on
      integer :: cmd_unit = input_unit    !! commands in
      integer :: rep_unit = 6             !! replies out
      integer :: days_done = 0            !! days completed
      integer :: days_total = 0           !! days in the simulation
      integer :: run_left = 0             !! days to run before the next pause
      logical :: run_free = .false.       !! run to the end without pausing
      integer :: protocol = 3             !! protocol version
      integer, parameter :: unit_max = 1000000   !! highest unit number branch looks at
      character(len=80) :: paused_line = ""      !! the last @paused reply
      character(len=16), parameter :: state_magic = "SWXSTATE-3"

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
      integer, dimension(:), allocatable :: cha_bmi      !! SWAT-deg channel number -> grid index

      !! HRU soil layers: grid index -> HRU number and layer; HRU number -> its first layer
      integer :: n_hly = 0
      integer, dimension(:), allocatable :: hly_hru
      integer, dimension(:), allocatable :: hly_lay
      integer, dimension(:), allocatable :: hru_hly1
      real, dimension(:), allocatable :: hly_tmp         !! deg C      |soil layer temperature set for the next day
      logical, dimension(:), allocatable :: hly_on

      !! the day's values SWAT+ zeroes when it prints them, kept at the end of the day
      real, dimension(:), allocatable :: pou_dmd_day     !! m3/day     |place of use demand
      real, dimension(:), allocatable :: pou_wdr_day     !! m3/day     |place of use withdrawal

      integer, parameter :: nvar = 14
      character(len=48), dimension(nvar) :: var_name = [character(len=48) ::     &
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
            "snowpack__liquid-equivalent_depth"]
      character(len=8), dimension(nvar) :: var_grid = [character(len=8) ::      &
            "cha", "cha", "cha", "aqu", "aqu", "aqu", "aqu", "rec", "pou", "pou",    &
            "cha", "cha", "hly", "hru"]
      character(len=8), dimension(nvar) :: var_units = [character(len=8) ::     &
            "m3 s-1", "m3", "m3 s-1", "m3", "m", "m3 s-1", "m s-1", "m3 s-1", "m3 s-1", "m3 s-1", &
            "m", "m3 s-1", "degC", "m"]
      character(len=3), dimension(nvar) :: var_role = [character(len=3) ::      &
            "out", "out", "out", "out", "out", "out", "out", "in", "in", "out",  &
            "out", "in", "in", "out"]

      contains

!!    --------------------------------------------------------------------
      subroutine exch_open

!!    called first in main: turns exchange mode on when SWAT+ is started
!!    with --exchange, moves console messages to exchange.log and opens
!!    the reply channel on standard output

      character(len=64) :: arg = ""
      integer :: i = 0
      integer :: ios = 0
      integer :: u = 0

      do i = 1, command_argument_count()
        call get_command_argument (i, arg)
        if (trim(arg) == "--exchange") exch_on = .true.
      end do
      if (.not. exch_on) return

      !! on Mac and Linux, replies get standard output to themselves and unit 6
      !! (the console) goes to a file; elsewhere they share standard output and
      !! the "@" at the start of each reply line keeps them apart
      open (newunit=u, file="/dev/stdout", action="write", status="old", iostat=ios)
      if (ios == 0) then
        rep_unit = u
        open (6, file="exchange.log", action="write", status="replace")
      end if

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

      integer :: iyr = 0
      integer :: yr = 0
      integer :: ndy = 0
      integer :: d0 = 0
      integer :: d1 = 0
      integer :: iv = 0
      integer :: ihru = 0
      integer :: ly = 0

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
      allocate (chx_flo(sp_ob%chandeg), source = 0.)
      allocate (chx_on(sp_ob%chandeg), source = .false.)
      allocate (chx_gain(sp_ob%chandeg), source = 0.)
      allocate (chx_loss(sp_ob%chandeg), source = 0.)
      allocate (chx_day(sp_ob%chandeg), source = 0.)
      allocate (cha_bmi(0:max(1, maxval([0, (ob(sp_ob1%chandeg + iv - 1)%num, iv = 1, sp_ob%chandeg)]))), source = 0)
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

      call reply ("@hello swatplus-exchange " // itoa(protocol))
      call reply ("@model " // trim(adjustl(prog)))
      call reply ("@build " // swx_build)
      call reply ("@start " // itoa(time%yrc_start) // " " // itoa(max(1, time%day_start)))
      call reply ("@days " // itoa(days_total))
      call send_grid ("cha", sp_ob1%chandeg, sp_ob%chandeg)
      call send_grid ("aqu", sp_ob1%aqu, sp_ob%aqu)
      call send_grid ("rec", sp_ob1%recall, sp_ob%recall)
      call send_pou_grid
      call send_grid ("hru", sp_ob1%hru, sp_ob%hru)
      call send_hly_grid
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

      integer :: i = 0
      integer :: irec = 0
      integer :: iob = 0

      if (.not. exch_on) return

      do i = 1, size(rec_flo)
        if (.not. rec_flo_on(i)) cycle
        irec = ob(sp_ob1%recall + i - 1)%num
        recall(irec)%hd(time%day,time%yrs)%flo = rec_flo(i)
        rec_flo_on(i) = .false.
      end do

      do i = 1, size(chx_flo)
        chx_gain(i) = 0.
        chx_loss(i) = 0.
        if (.not. chx_on(i)) cycle
        if (chx_flo(i) > 0.) then
          !! a gain joins the channel's inflow through SWAT+'s transfer hydrograph
          iob = sp_ob1%chandeg + i - 1
          call trans_cs_ready (iob)
          ob(iob)%trans%flo = ob(iob)%trans%flo + chx_flo(i)
          chx_gain(i) = chx_flo(i)
        else if (chx_flo(i) < 0.) then
          chx_loss(i) = - chx_flo(i)
        end if
        chx_on(i) = .false.
      end do

      do i = 1, size(dmd_flo)
        if (.not. dmd_flo_on(i)) cycle
        call set_demand (i, dmd_flo(i))
        dmd_flo_on(i) = .false.
      end do

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
!!    no more than is there, and counts it as seepage so SWAT+'s printed
!!    channel balance still closes; records what was exchanged

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
      if (chx_loss(i) > 0. .and. ht2%flo > 0.) then
        rto = Min(1., chx_loss(i) / ht2%flo)
        taken = rto * ht2%flo
        ht2 = (1. - rto) * ht2
        ch_wat_d(ich)%seep = ch_wat_d(ich)%seep + taken
      end if
      chx_day(i) = chx_gain(i) - taken
      chx_gain(i) = 0.
      chx_loss(i) = 0.

      return
      end subroutine exch_channel

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

      integer :: i = 0

      if (.not. exch_on) return

      do i = 1, size(pou_wdr_day)
        pou_dmd_day(i) = poud_met(i)%duty_tot%duty
        pou_wdr_day(i) = poud_met(i)%duty_tot%deliv
      end do
      !! a state set for the day applies that day only, even for an HRU the
      !! day's commands didn't run
      hly_on = .false.

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
      real(8) :: v = 0.d0

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

        case ("set")
          read (line, *, iostat=ios) word, name, i, v
          if (ios /= 0) then
            call reply ("@error set needs a variable name, a 1-based index and a value")
          else if (at_end) then
            call reply ("@error the simulation has ended; nothing left to set")
          else
            call set_value (trim(name), i, v)
          end if

        case ("setv", "seti")
          read (line, *, iostat=ios) word, name, n
          if (ios /= 0 .or. n < 0) then
            call reply ("@error " // trim(word) // " needs a variable name and a value count")
          else if (at_end) then
            !! read the values anyway, so the next command isn't one of them
            do i = 1, n
              read (cmd_unit, '(a)', iostat=ios) line
            end do
            call reply ("@error the simulation has ended; nothing left to set")
          else
            call set_values (trim(name), n, trim(word) == "seti")
          end if

        case ("ping")
          call reply ("@ok")

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
!!    Linux only.

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

      !! the child: leave the parent's channels and folder
      close (rep_unit)
      if (swx_chdir (trim(new_dir) // c_null_char) /= 0) error stop 4
      ios = swx_detach ("exchange.err" // c_null_char)
      open (6, file="exchange.log", action="write", position="append")
      write (6,*) "branched from ", trim(old_dir), " after day ", days_done
      call close_units (trim(old_dir))
      call reopen_closed
      open (newunit=cmd_unit, file=trim(fifo_in), action="read", status="old")
      open (newunit=rep_unit, file=trim(fifo_out), action="write", status="old")
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
        if (nm(1:1) == "/") then
          if (.not. (len_trim(nm) > n + 1 .and. nm(1:n) == run_dir(1:n) .and. nm(n+1:n+1) == "/")) then
            msg = trim(nm) // " is outside the run folder (out_path in file.cio?)"
            return
          end if
        else if (nm(1:3) == "../") then
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
          if (nm(1:n) == old_dir(1:n) .and. nm(n+1:n+1) == "/") nm = nm(n+2:)
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
      write (u) size(chx_day), chx_day
      write (u) size(rec_flo), rec_flo, rec_flo_on, dmd_flo, dmd_flo_on, chx_flo, chx_on
      write (u) n_hly, hly_tmp, hly_on
      call swx_state_save (u)
      write (u) state_magic
      close (u)
      call reply ("@ok")

      return
      end subroutine save_state

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
      if (ios /= 0 .or. magic /= state_magic) then
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
      character(len=16) :: digest = ""
      character(len=64) :: word = ""
      integer :: u = 0
      integer :: n = 0
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
      if (n /= size(chx_day)) then
        call reply ("@error the saved state has a different number of channels")
        error stop 7
      end if
      read (u) chx_day
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
      read (u) hly_tmp, hly_on
      call swx_state_load (u)
      read (u) magic
      close (u)
      if (magic /= state_magic) then
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

      return
      end subroutine load_now

!!    --------------------------------------------------------------------
      subroutine send_values (name)

      use hydrograph_module, only : sp_ob, sp_ob1, ob, ch_in_d, ch_out_d, ch_stor
      use aquifer_module, only : aqu_d
      use maximum_data_module, only : db_mx
      use soil_module, only : soil
      use hru_module, only : hru

      character(len=*), intent(in) :: name
      real(8), dimension(:), allocatable :: val
      integer :: i = 0
      integer :: iob = 0

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

      case ("snowpack__liquid-equivalent_depth")
        allocate (val(sp_ob%hru), source = 0.d0)
        do i = 1, sp_ob%hru
          iob = sp_ob1%hru + i - 1
          val(i) = hru(ob(iob)%num)%sno_mm / 1000.d0       !mm -> m
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

      case default
        call reply ("@error unknown variable: " // name)
        return
      end select

      call send_array (val)

      return
      end subroutine send_values

!!    --------------------------------------------------------------------
      subroutine take_value (name, i, v, keep, msg)

!!    checks a value for the next day, and keeps it if keep is true; msg says
!!    why it is refused, blank if it isn't. Refused when SWAT+ would not use
!!    it as given: a point source that isn't daily or has no record in the
!!    next day's year, a groundwater exchange with sub-daily routing or on a
!!    channel of no length, a value that isn't a finite number

      use time_module
      use hydrograph_module, only : sp_ob, sp_ob1, ob, recall
      use maximum_data_module, only : db_mx

      character(len=*), intent(in) :: name
      integer, intent(in) :: i
      real(8), intent(in) :: v                            !m3/s, or deg C
      logical, intent(in) :: keep
      character(len=*), intent(out) :: msg
      integer :: irec = 0

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
        if (keep) then
          rec_flo(i) = real(v * 86400.d0)                   !m3/s -> m3/day
          rec_flo_on(i) = .true.
        end if

      case ("channel_water__groundwater_volume_exchange_rate")
        call check_exchange (i, msg)
        if (msg /= "") return
        if (keep) then
          chx_flo(i) = real(v * 86400.d0)                   !m3/s -> m3/day
          chx_on(i) = .true.
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

      case default
        msg = "not a settable variable: " // name
        return
      end select

      return
      end subroutine take_value

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

!!    setv <var> <n>, then n lines <v>: every object on the variable's grid,
!!    in grid order; seti <var> <n>, then n lines <i> <v>: the objects given.
!!    All or nothing: if any value is refused, none is kept, and the reply
!!    names the first one refused.

      character(len=*), intent(in) :: name
      integer, intent(in) :: n
      logical, intent(in) :: indexed
      character(len=400) :: msg = ""
      character(len=128) :: line = ""
      integer, dimension(:), allocatable :: idx
      real(8), dimension(:), allocatable :: val
      integer :: k = 0
      integer :: ios = 0
      integer :: bad = 0

      allocate (idx(max(n, 0)), source = 0)
      allocate (val(max(n, 0)), source = 0.d0)
      bad = 0
      do k = 1, n
        read (cmd_unit, '(a)', iostat=ios) line
        if (ios /= 0) error stop 3
        if (indexed) then
          read (line, *, iostat=ios) idx(k), val(k)
        else
          idx(k) = k
          read (line, *, iostat=ios) val(k)
        end if
        if (ios /= 0 .and. bad == 0) then
          bad = k
          msg = "can't read the value"
        end if
      end do
      if (bad == 0 .and. .not. indexed .and. n /= var_size(name)) then
        call reply ("@error setv needs all " // itoa(var_size(name)) // " values of " // name)
        return
      end if
      do k = 1, n
        if (bad /= 0) exit
        call take_value (name, idx(k), val(k), .false., msg)
        if (msg /= "") bad = k
      end do
      if (bad /= 0) then
        call reply ("@error value " // itoa(bad) // ": " // trim(msg))
        return
      end if
      do k = 1, n
        call take_value (name, idx(k), val(k), .true., msg)
      end do
      call reply ("@ok")

      return
      end subroutine set_values

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
        case ("pou"); var_size = db_mx%wallo_pou
        case ("hru"); var_size = sp_ob%hru
        case ("hly"); var_size = n_hly
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

!!    one line: @value n v1 ... vn, seventeen significant digits so an
!!    8-byte real goes out and comes back unchanged

      real(8), dimension(:), intent(in) :: val
      integer :: i = 0

      write (rep_unit, '(a,i0)', advance="no") "@value ", size(val)
      do i = 1, size(val)
        write (rep_unit, '(1x,es24.16e3)', advance="no") val(i)
      end do
      write (rep_unit, '(a)') ""
      flush (rep_unit)

      return
      end subroutine send_array

!!    --------------------------------------------------------------------
      subroutine reply (line)

      character(len=*), intent(in) :: line

      write (rep_unit, '(a)') trim(line)
      flush (rep_unit)

      return
      end subroutine reply

!!    --------------------------------------------------------------------
      function itoa (i) result (s)

      integer, intent(in) :: i
      character(len=:), allocatable :: s
      character(len=24) :: buf

      write (buf, '(i0)') i
      s = trim(buf)

      end function itoa

      end module bmi_exchange_module
