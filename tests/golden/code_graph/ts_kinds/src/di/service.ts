import { Dep } from './dep';

@Injectable()
export class Service {
  constructor(private readonly dep: Dep) {}
}
